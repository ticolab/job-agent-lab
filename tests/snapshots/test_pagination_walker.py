"""Termination-behaviour tests for the SYS-5 pagination walker.

The walker in :mod:`vacantes.extraction.dom.collector` composes
discovery, click, settle, and per-state collection into a loop with
five terminating conditions:

1. Discovery finds no next-control on the current state.
2. The stamped ``[data-jal-next]`` click raises.
3. The settle poll times out (click produced no observable change).
4. The new state contributes zero URLs not already in the union
   (cyclic wrap-around).
5. The ``max_pages`` cap is reached.

This module drives the *production* :func:`walk_and_collect` against
tiny, self-contained ``file://`` sites — one per test — where each page
has a known anchor set and a known pagination affordance. That gives
the walker real anchor navigation (trusted clicks, real DOM re-loads,
real settle transitions) without any live network or fixture-directory
fixtures on disk.

Playwright integration notes:

- ``tests/snapshots/conftest.py`` disables page JavaScript at the
  browser-context level for the snapshot regression harness. That
  override applies only to fixtures the pytest-playwright plugin auto-
  creates. This module builds its own JS-enabled context via
  ``browser.new_context(java_script_enabled=True)`` so the anchor
  clicks trigger real navigation (which requires the page's own JS
  event dispatch).
- The walker is ``async``; pytest-playwright ships the sync API only,
  and its fixtures leave an ambient event loop entry on the test
  thread (via ``greenlet``) that makes ``asyncio.run`` refuse to
  start a new loop. The sibling ``test_greenhouse.py``'s
  worker-thread pattern cannot be reused either: sync Playwright is
  thread-affine, so ``page.evaluate`` from a different thread hangs
  or errors on the greenlet switch.
- The workaround is to drive the walker coroutine manually. The
  walker's only ``await`` points are (a) into the tiny
  :class:`_TestDriver` shim below, whose methods are ``async def``s
  with no ``await`` inside (so they complete via ``StopIteration`` on
  the first ``.send(None)``), and (b) ``asyncio.sleep(...)`` calls in
  the collector module. The :func:`fast_walker` fixture monkeypatches
  those sleep points to a synchronous ``time.sleep``-backed fake, so
  no coroutine ever yields to a scheduler. :func:`_drive` iterates
  ``.send(None)`` and returns the value from ``StopIteration``.
"""

from __future__ import annotations

import time
from collections.abc import Coroutine, Iterable
from pathlib import Path
from typing import Any, cast

import pytest
from playwright.sync_api import Browser, BrowserContext, Page

from vacantes.extraction.dom import collector as collector_mod
from vacantes.extraction.dom.collector import walk_and_collect

_PREFIX = "/jobs"


# ---------------------------------------------------------------------------
# Manual coroutine driver
# ---------------------------------------------------------------------------


def _drive(coro: Coroutine[Any, Any, Any]) -> Any:
    """Drive ``coro`` to completion without any real event loop.

    Preconditions (all upheld by this module's tests):

    - ``coro`` and every coroutine it awaits contain no real
      scheduler yield points — every ``await`` must resolve
      synchronously (``async def`` with no inner ``await``, or an
      ``async def`` that only awaits similarly synchronous coroutines).
    - The ``fast_walker`` fixture has replaced
      ``collector_mod.asyncio.sleep`` with a synchronous fake so the
      walker's ``asyncio.sleep(...)`` awaits do not yield either.

    If any awaited object *does* yield (e.g. a real Future) this
    function raises ``AssertionError`` — a loud signal that the test
    setup drifted and needs to switch to a real event-loop harness.
    """
    try:
        yielded = coro.send(None)
    except StopIteration as stop:
        return stop.value

    # Reaching here means the coroutine handed a value out to a
    # scheduler that does not exist in this test setup. Failing
    # loudly is preferable to hanging.
    raise AssertionError(
        "walker coroutine yielded to a scheduler "
        f"(value={yielded!r}); test setup expected all awaits to "
        "resolve synchronously — did fast_walker fail to monkeypatch "
        "asyncio.sleep, or did the collector add a new await point?"
    )


# ---------------------------------------------------------------------------
# Test driver
# ---------------------------------------------------------------------------


class _TestDriver:
    """Async ``PageDriver`` shim over a sync Playwright ``Page``.

    ``walk_and_collect`` is ``async`` because the runtime uses browser-
    use's async page handle. The sync Playwright API gives blocking
    method calls; wrapping each in an ``async def`` that forwards
    directly to the sync call is safe under ``asyncio.run`` (there is
    no true concurrency in these tests — one page, one walker) and
    keeps the tests using the ergonomic pytest-playwright fixtures.
    """

    def __init__(self, page: Page) -> None:
        self._page = page

    async def evaluate(self, js: str, arg: Any) -> Any:
        # Sync playwright already parses JS return values.
        return self._page.evaluate(js, arg)

    async def click(self, selector: str) -> None:
        # Playwright's sync ``page.click`` auto-waits for any
        # navigation triggered by the click to settle. This satisfies
        # the walker's :class:`PageDriver` contract; the runtime's
        # :class:`ActorPageDriver` reaches the same observable
        # behaviour via a JS ``HTMLElement.click()`` (see the
        # PageDriver docstring for why the CDP-mouse path is
        # unreliable on the browser-use runtime).
        self._page.click(selector)

    async def url(self) -> str:
        # Assign-then-return, matching ActorPageDriver.url — playwright's
        # ``page.url`` is typed ``Any``, and mypy-strict rejects an
        # implicit narrow at the return site.
        result: str = self._page.url
        return result


# ---------------------------------------------------------------------------
# Site builders
# ---------------------------------------------------------------------------


def _write_page(
    path: Path,
    *,
    job_ids: Iterable[str],
    next_href: str | None,
    next_kind: str = "text",
) -> None:
    """Write one paginated page under ``path``.

    Each ``job_id`` renders as ``<a href="/jobs/<id>">``. When
    ``next_href`` is None, no pagination affordance is rendered — the
    walker will hit the no-control termination on that state. When
    provided, ``next_kind`` controls the shape (``"text"`` → bare
    ``<a>Next</a>``, ``"rel"`` → ``<a rel="next">go</a>``, ``"button"``
    → ``<button>Next</button>`` — used for the settle-timeout test
    since a button without JS does nothing on click).
    """
    _nl = chr(10)  # a literal linefeed; avoids a raw newline in the source
    anchors = _nl.join(f'<a href="/jobs/{jid}">Job {jid}</a>' for jid in job_ids)

    pager = ""
    if next_href is not None:
        if next_kind == "text":
            pager = f'<a href="{next_href}">Next</a>'
        elif next_kind == "rel":
            pager = f'<a rel="next" href="{next_href}">go</a>'
        elif next_kind == "button":
            # No onclick handler → click is a no-op → walker settle-times-out.
            pager = "<button>Next</button>"
        else:  # pragma: no cover - defensive
            raise ValueError(f"unknown next_kind: {next_kind!r}")

    # ``<base href="file:///">`` makes ``/jobs/x`` resolve to the
    # ``file://`` origin the walker's matcher is configured with.
    path.write_text(
        f'<!DOCTYPE html><html><head><base href="file:///"></head>'
        f"<body>{anchors}{_nl}{pager}</body></html>",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def js_context(browser: Browser) -> Iterable[BrowserContext]:
    """A JS-enabled context; the file scope's JS-off override is bypassed.

    The snapshot regression harness's ``browser_context_args`` fixture
    disables page JS on every auto-created context. That override does
    not apply to contexts constructed explicitly, so we make one here
    with JS on — necessary because the walker's advance relies on real
    anchor-click navigation which is a JS event on the page side.
    """
    ctx = browser.new_context(java_script_enabled=True)
    try:
        yield ctx
    finally:
        ctx.close()


@pytest.fixture
def js_page(js_context: BrowserContext) -> Iterable[Page]:
    page = js_context.new_page()
    try:
        yield page
    finally:
        page.close()


@pytest.fixture
def fast_walker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink walker timing constants and disable real asyncio.sleep yields.

    The production defaults (``POLL_INTERVAL_SEC=0.5``,
    ``SETTLE_TIMEOUT_SEC=10.0``, ``MOUNT_GRACE_SEC=1.0``) are tuned for
    real SPAs. Tests using ``file://`` pages settle instantly, so we
    drop them to millisecond-scale values. ``MAX_PAGES`` is left alone
    — the cap test explicitly uses the ``max_pages`` kwarg.

    Also replaces the collector's ``asyncio.sleep`` with a synchronous
    fake so :func:`_drive` never encounters a scheduler yield. Real
    wallclock time still passes (via ``time.sleep``) so the settle-
    timeout branch of :func:`walk_and_collect` still terminates.
    """
    monkeypatch.setattr(collector_mod, "POLL_INTERVAL_SEC", 0.02)
    monkeypatch.setattr(collector_mod, "SETTLE_TIMEOUT_SEC", 1.0)
    monkeypatch.setattr(collector_mod, "MOUNT_GRACE_SEC", 0.02)

    async def _sync_sleep(seconds: float) -> None:
        # ``async def`` with no ``await`` returns immediately via
        # ``StopIteration`` when driven with ``.send(None)`` — the
        # ``time.sleep`` is inside the coroutine body so real
        # wallclock still advances for tests that depend on it.
        time.sleep(seconds)

    monkeypatch.setattr(collector_mod.asyncio, "sleep", _sync_sleep)


# ---------------------------------------------------------------------------
# Walker runner
# ---------------------------------------------------------------------------


async def _walk(
    page: Page,
    *,
    max_pages: int | None = None,
    next_control_override: str | None = None,
) -> tuple[set[str], list[int], str]:
    """Run ``walk_and_collect`` against ``page`` and record ``on_state`` calls.

    Returns ``(urls, states, observed_origin)``. The origin is probed
    from the loaded page rather than hard-coded because Chromium
    reports ``URL.origin`` as the opaque string ``"null"`` for
    ``file://`` URLs (per WHATWG), and the matcher's origin gate is a
    strict-equality check — a mismatched ``careerOrigin`` would silently
    drop every anchor.
    """
    driver = _TestDriver(page)
    origin: str = cast(str, page.evaluate("() => new URL(location.href).origin"))
    states: list[int] = []

    async def _record(state_idx: int) -> None:
        states.append(state_idx)

    kwargs: dict[str, Any] = {"on_state": _record}
    if max_pages is not None:
        kwargs["max_pages"] = max_pages
    if next_control_override is not None:
        kwargs["next_control_override"] = next_control_override

    urls = await walk_and_collect(driver, _PREFIX, origin, 1, **kwargs)
    return urls, states, origin


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestThreePageWalk:
    """Happy path: 3 pages of distinct jobs, walker unions all three."""

    def test_full_walk(self, tmp_path: Path, js_page: Page, fast_walker: None) -> None:
        p1 = tmp_path / "page-1.html"
        p2 = tmp_path / "page-2.html"
        p3 = tmp_path / "page-3.html"
        _write_page(p1, job_ids=["a", "b"], next_href=str(p2))
        _write_page(p2, job_ids=["c", "d"], next_href=str(p3))
        _write_page(p3, job_ids=["e"], next_href=None)

        js_page.goto(p1.as_uri())
        urls, states, _origin = _drive(_walk(js_page))

        assert urls == {
            "file:///jobs/a",
            "file:///jobs/b",
            "file:///jobs/c",
            "file:///jobs/d",
            "file:///jobs/e",
        }
        # ``on_state`` fires once per successfully collected state: 3.
        assert states == [1, 2, 3]


class TestCyclicPagination:
    """Page N's Next wraps to page 1: zero-new-links termination."""

    def test_wrap_terminates_without_double_count(
        self, tmp_path: Path, js_page: Page, fast_walker: None
    ) -> None:
        p1 = tmp_path / "page-1.html"
        p2 = tmp_path / "page-2.html"
        _write_page(p1, job_ids=["a", "b"], next_href=str(p2))
        # Page 2's "Next" wraps to page 1 — every anchor on the resulting
        # state is already in the union, so the walker sees zero new
        # links and stops. Without this guard a naïve loop would spin
        # indefinitely between the two pages.
        _write_page(p2, job_ids=["c", "d"], next_href=str(p1))

        js_page.goto(p1.as_uri())
        urls, states, _origin = _drive(_walk(js_page))

        assert urls == {
            "file:///jobs/a",
            "file:///jobs/b",
            "file:///jobs/c",
            "file:///jobs/d",
        }
        # State 1 and state 2 are collected; the walk to page 3 (which
        # would be page 1 again) is aborted after the final collect
        # produces zero new URLs, so on_state fires 2×.
        assert states == [1, 2]


class TestNoPager:
    """A single-state site with no Next: walker returns state 1 only."""

    def test_single_state(
        self, tmp_path: Path, js_page: Page, fast_walker: None
    ) -> None:
        p1 = tmp_path / "page-1.html"
        _write_page(p1, job_ids=["only-one", "another"], next_href=None)

        js_page.goto(p1.as_uri())
        urls, states, _origin = _drive(_walk(js_page))

        assert urls == {"file:///jobs/only-one", "file:///jobs/another"}
        assert states == [1]


class TestMaxPagesCap:
    """A chain longer than ``max_pages`` stops at the cap.

    The default cap is :data:`MAX_PAGES` = 20. Rather than build 25
    pages on disk for every test run, we use the ``max_pages`` kwarg
    to pin a smaller cap and verify the walker honours it — the cap
    semantics are identical regardless of the concrete integer, and
    ``max_pages`` is the parameter capture callers thread through.
    """

    def test_walker_stops_at_max_pages(
        self, tmp_path: Path, js_page: Page, fast_walker: None
    ) -> None:
        # Build a 6-page chain but cap the walker at 3. Pages beyond
        # the cap exist and would happily contribute new links, so a
        # broken cap would immediately show as extra URLs.
        pages = [tmp_path / f"page-{i}.html" for i in range(1, 7)]
        for i, current in enumerate(pages):
            next_href = str(pages[i + 1]) if i + 1 < len(pages) else None
            _write_page(current, job_ids=[f"p{i + 1}"], next_href=next_href)

        js_page.goto(pages[0].as_uri())
        urls, states, _origin = _drive(_walk(js_page, max_pages=3))

        # Exactly 3 states collected: pages 1, 2, 3. Pages 4-6 are
        # never visited so their anchors are not in the union.
        assert urls == {
            "file:///jobs/p1",
            "file:///jobs/p2",
            "file:///jobs/p3",
        }
        assert states == [1, 2, 3]


class TestSettleTimeout:
    """A click that produces no observable change terminates cleanly."""

    def test_button_pager_with_no_handler_times_out(
        self, tmp_path: Path, js_page: Page, fast_walker: None
    ) -> None:
        # A ``<button>Next</button>`` with no click handler passes
        # discovery (visible, enabled, tag=BUTTON, text=/^next$/i) but
        # its click does nothing — the DOM state does not change. The
        # walker's settle poll times out on the shrunk
        # ``SETTLE_TIMEOUT_SEC=1.0`` and returns state-1 results only.
        p1 = tmp_path / "page-1.html"
        _write_page(p1, job_ids=["x", "y"], next_href="", next_kind="button")

        js_page.goto(p1.as_uri())
        urls, states, _origin = _drive(_walk(js_page))

        # Only state 1's anchors were collected; the state-2 attempt
        # was aborted by the settle timeout, so ``on_state`` fires 1×.
        assert urls == {"file:///jobs/x", "file:///jobs/y"}
        assert states == [1]


# ---------------------------------------------------------------------------
# SYS-12: next_control_override bypass path
# ---------------------------------------------------------------------------


def _write_override_page(
    path: Path,
    *,
    job_ids: Iterable[str],
    next_href: str | None,
    decoy_label: str = "Next",
    override_label: str = "More jobs",
) -> None:
    """Write a page with two Next-shaped controls: a decoy that would win
    the six-signal cascade and a distinct ``[data-role=pager]`` anchor
    that only a CSS override can select.

    The decoy is a bare ``<a>Next</a>`` (matches signal 3 — text
    ``next``). The override target is ``<a data-role="pager">More
    jobs</a>`` — its trimmed text does not match any of the six
    signals (``more jobs`` fails the next-allowlist token check, and
    ``load more`` requires the ``load|show`` prefix). Its href
    resolves to ``next_href``; the decoy points nowhere.

    If ``next_href`` is None, neither pager is rendered — used for the
    "override with no match" test where the target attribute is
    absent so ``querySelector`` returns null.
    """
    _nl = chr(10)
    anchors = _nl.join(f'<a href="/jobs/{jid}">Job {jid}</a>' for jid in job_ids)

    pagers = ""
    if next_href is not None:
        # Decoy: bare anchor with text matching signal 3 but no href
        # that advances state. If the override branch fails to fire,
        # the walker would stamp+click this and settle-timeout.
        pagers = (
            f'<a href="#decoy">{decoy_label}</a>{_nl}'
            f'<a data-role="pager" href="{next_href}">{override_label}</a>'
        )

    path.write_text(
        f'<!DOCTYPE html><html><head><base href="file:///"></head>'
        f"<body>{anchors}{_nl}{pagers}</body></html>",
        encoding="utf-8",
    )


class TestNextControlOverrideSelected:
    """When the override is set, the walker stamps the CSS-selected
    control and ignores the six-signal cascade winner."""

    def test_override_wins_over_decoy(
        self, tmp_path: Path, js_page: Page, fast_walker: None
    ) -> None:
        p1 = tmp_path / "page-1.html"
        p2 = tmp_path / "page-2.html"
        _write_override_page(p1, job_ids=["a", "b"], next_href=str(p2))
        _write_page(p2, job_ids=["c"], next_href=None)

        js_page.goto(p1.as_uri())
        urls, states, _origin = _drive(
            _walk(js_page, next_control_override="a[data-role=pager]")
        )

        # If the override fired correctly, we advanced to page 2 and
        # collected ``c`` alongside page 1's ``a, b``. If the cascade
        # had won, the decoy anchor's ``#decoy`` fragment click would
        # not have changed the DOM and the walker would settle-timeout
        # at state 1 with only {a, b}.
        assert urls == {
            "file:///jobs/a",
            "file:///jobs/b",
            "file:///jobs/c",
        }
        assert states == [1, 2]


class TestNextControlOverrideIgnoredWhenNone:
    """With no override (default), the walker's six-signal cascade
    runs unchanged — regression guard on the None-passthrough."""

    def test_cascade_runs_without_override(
        self, tmp_path: Path, js_page: Page, fast_walker: None
    ) -> None:
        p1 = tmp_path / "page-1.html"
        p2 = tmp_path / "page-2.html"
        # Only the decoy (text=Next) is present — no override target.
        # The cascade must pick it and advance.
        _write_page(p1, job_ids=["a"], next_href=str(p2), next_kind="text")
        _write_page(p2, job_ids=["b"], next_href=None)

        js_page.goto(p1.as_uri())
        urls, states, _origin = _drive(_walk(js_page))  # no override kwarg

        assert urls == {"file:///jobs/a", "file:///jobs/b"}
        assert states == [1, 2]


class TestNextControlOverrideNoMatch:
    """When the override is set but ``querySelector`` returns null,
    the JS asset returns ``{found: false}`` and the walker terminates
    at state 1 — same branch as a signal-cascade miss."""

    def test_no_match_terminates_at_state_one(
        self, tmp_path: Path, js_page: Page, fast_walker: None
    ) -> None:
        p1 = tmp_path / "page-1.html"
        # A decoy IS present in the DOM (text=Next → signal 3 would
        # fire in the default cascade), but the override selector
        # targets an attribute that no element in the page carries.
        # The override path must NOT fall back to the cascade — it
        # must return not-found and terminate the walker.
        _write_page(p1, job_ids=["a", "b"], next_href="#decoy", next_kind="text")

        js_page.goto(p1.as_uri())
        urls, states, _origin = _drive(
            _walk(js_page, next_control_override="a[data-role=pager]")
        )

        # State 1 only — override missed and no fallback to cascade.
        assert urls == {"file:///jobs/a", "file:///jobs/b"}
        assert states == [1]
