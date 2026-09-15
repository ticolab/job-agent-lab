"""Event-loop harness shared by the async unit tests.

Not a test module — it holds the machinery that lets a coroutine be
driven from a synchronous test body without ``pytest-asyncio``.

**Why the main thread's loop is unusable.** The snapshot suite drives
Chromium through Playwright's *sync* API, which leaves a running-loop
registration on the main thread's :mod:`asyncio.events` state. After
that, both ``asyncio.run`` and a main-thread ``run_until_complete``
raise for the rest of the session. A full-suite run therefore cannot
drive coroutines on the main thread at all, and a test that passes in
isolation would fail in CI. :mod:`tests.unit.test_prefiltered` carries
the original diagnosis; this module is the shared implementation.

**Why engine disposal lives here.** An async SQLAlchemy engine that
outlives its event loop leaves aiosqlite's background thread holding a
reference to a closed loop. The failure surfaces as an unhandled thread
exception attributed to whichever test happens to run next, which is
about as hard to diagnose as it sounds. :func:`run_async` disposes every
tracked engine on the same loop that created it, immediately before
closing that loop, so no test has to remember to.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

# Engines opened by the scenario currently running. A module-level list
# is safe because tests run sequentially and each scenario gets its own
# thread, so only one is ever in flight.
_OPEN_ENGINES: list[AsyncEngine] = []


def track_engine(engine: AsyncEngine) -> AsyncEngine:
    """Register *engine* for disposal when the current scenario ends.

    Returns the engine, so it can wrap a construction expression
    directly.
    """
    _OPEN_ENGINES.append(engine)
    return engine


async def _dispose_tracked_engines() -> None:
    """Dispose every engine the current scenario opened."""
    while _OPEN_ENGINES:
        await _OPEN_ENGINES.pop().dispose()


def run_in_thread[T](fn: Callable[[], T]) -> T:
    """Run *fn* on a background thread, re-raising anything it raises.

    Public because coroutines are not the only thing that needs to stay
    off the main thread: a *synchronous* callable that calls
    ``asyncio.run`` internally — Alembic's async ``env.py`` does exactly
    that — hits the same tainted main-thread loop state and needs the
    same treatment.
    """
    box: dict[str, Any] = {}

    def _thread_main() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 — re-raised below
            box["error"] = exc

    thread = threading.Thread(target=_thread_main)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box["value"]  # type: ignore[no-any-return]


def run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    """Drive *coro* to completion on a background thread's own loop.

    Each test body should be a *single* coroutine, so everything it
    creates is created, used, and disposed inside one event loop.
    """

    def _main() -> T:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.run_until_complete(_dispose_tracked_engines())
            loop.close()

    return run_in_thread(_main)
