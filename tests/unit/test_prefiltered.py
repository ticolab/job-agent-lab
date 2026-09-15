"""Unit tests for the SYS-13 agent-less multi-state union path.

Drives :meth:`DomStrategy._extract_prefiltered` directly with a stub
:class:`BrowserSession` and monkeypatched
:func:`collect_job_links` / :func:`plausible_headless_ua`, so no
network is opened and no Chromium binary is launched. The five
acceptance-criteria bullets in ``spike/SYS_13_PLAN.md`` Task 4 map to
one test class each below.

``asyncio.sleep`` is monkeypatched to an immediate no-op at the
``strategy`` module's re-bound symbol (walker-test precedent) so the
capture-mirroring settle loop
(``RENDER_WAIT_SEC`` + ``RENDER_SCROLL_COUNT`` iterations) collapses to
microseconds and the tests stay in the sub-second range.

Async tests are driven via a small :func:`_run` helper that runs each
coroutine on a background thread with its own fresh event loop. The
repo does not use ``pytest-asyncio``, and the greenhouse-strategy test
file establishes the bare ``asyncio.run`` pattern for the isolated
case. This file cannot use ``asyncio.run`` (or a main-thread
``new_event_loop`` + ``run_until_complete``) because the snapshot
suite (:mod:`tests.snapshots.test_extractor_snapshots` and friends)
runs earlier in the full session and drives Chromium through
Playwright's sync API, which leaves a *running-loop* registration on
the main thread's ``asyncio.events`` state even after every snapshot
test has torn down. That registration causes both ``asyncio.run`` and
``loop.run_until_complete`` to raise "cannot be called from a running
event loop" / "Cannot run the event loop while another loop is
running". Running on a background thread with its own loop sidesteps
the tainted main-thread state entirely and is safe here because
:meth:`DomStrategy._extract_prefiltered` and every one of its stubbed
dependencies is thread-independent (no shared mutable objects, no
Playwright handles, no browser-use ``Agent``).
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from typing import Any

import pytest

from vacantes.domain.company import Company, LinkRule, RuntimeHooks
from vacantes.domain.region import COSTA_RICA_LATAM
from vacantes.extraction.base import RunContext
from vacantes.extraction.dom import strategy as strategy_mod
from vacantes.extraction.dom.strategy import DomStrategy


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    """Drive an async coroutine to completion on a background thread.

    See the module docstring for the rationale — pytest-playwright's
    sync-loop machinery leaves a running-loop registration on the main
    thread's :mod:`asyncio.events` state that we cannot reliably clear
    from this side. Running on a separate thread with a fresh
    :func:`asyncio.new_event_loop` gives us an isolated ``asyncio``
    state that :meth:`AbstractEventLoop.run_until_complete` accepts.

    Exceptions raised inside the coroutine are re-raised on the caller
    side so pytest sees them as normal test failures.
    """
    result: dict[str, Any] = {}

    def _thread_main() -> None:
        loop = asyncio.new_event_loop()
        try:
            result["value"] = loop.run_until_complete(coro)
        except BaseException as exc:  # noqa: BLE001 — re-raised below
            result["error"] = exc
        finally:
            loop.close()

    thread = threading.Thread(target=_thread_main)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result["value"]  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubPage:
    """Minimal stub returned by :meth:`_StubBrowserSession.get_current_page`.

    The only method :meth:`_extract_prefiltered` invokes on the page is
    ``evaluate`` — inside the ``RENDER_SCROLL_COUNT`` scroll loop, with
    the ``window.scrollBy(0, window.innerHeight)`` JS. The stub swallows
    the call and returns ``None``.
    """

    def __init__(self) -> None:
        self.evaluate_calls: list[str] = []

    async def evaluate(self, js: str, *args: Any) -> Any:
        self.evaluate_calls.append(js)
        return None


class _StubBrowserSession:
    """Stub :class:`BrowserSession` recording the navigate/start/stop trace.

    Constructed by :func:`_make_patched`'s fake factory (which replaces
    ``strategy_mod.BrowserSession``); every field is a plain observable
    the tests read after the run.

    ``raise_on_navigate`` is an optional pre-seeded exception. When
    non-``None`` it is raised on the first ``navigate_to`` call — used
    by the error-path test to verify the ``finally`` still runs
    ``stop``.
    """

    def __init__(self, browser_profile: Any = None) -> None:
        self.browser_profile = browser_profile
        self.navigate_calls: list[str] = []
        self.start_count = 0
        self.stop_count = 0
        self._page: _StubPage = _StubPage()
        self.raise_on_navigate: Exception | None = None

    async def start(self) -> None:
        self.start_count += 1

    async def stop(self) -> None:
        self.stop_count += 1

    async def navigate_to(self, url: str) -> None:
        self.navigate_calls.append(url)
        if self.raise_on_navigate is not None:
            raise self.raise_on_navigate

    async def get_current_page(self) -> _StubPage:
        return self._page


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ctx() -> RunContext:
    """A minimal :class:`RunContext` — the prefiltered path reads only ``headless``."""
    return RunContext(
        model="gpt-4.1-mini",
        headless=True,
        max_steps=25,
        region=COSTA_RICA_LATAM,
    )


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Install the standard stubs on ``strategy`` module.

    Returns a mutable ``state`` dict tests can:

    - Seed with per-state collector return values via ``state["states"]``
      (a list-of-lists; index N is served on the N-th collector call).
    - Read the constructed :class:`_StubBrowserSession` instance via
      ``state["session"]`` (populated when
      :meth:`_extract_prefiltered` constructs the session).
    - Read the recorded ``collect_job_links`` per-call kwargs via
      ``state["collect_calls"]``.
    """
    state: dict[str, Any] = {
        "session": None,
        "states": [],
        "collect_calls": [],
    }

    def fake_session_factory(*, browser_profile: Any = None) -> _StubBrowserSession:
        sess = _StubBrowserSession(browser_profile=browser_profile)
        state["session"] = sess
        return sess

    async def fake_ua() -> str:
        return "TestAgent/1.0 (Chrome/999)"

    async def fake_collect(
        session: Any,
        base_path: str,
        origin: str,
        *,
        min_depth: int,
        paginate: bool,
        hooks: Any,
        suppress_selector: str | None = None,
    ) -> list[str]:
        # The keyword-only signature deliberately mirrors the real
        # ``collect_job_links``: a production call that grows a new
        # keyword fails loudly here rather than silently going
        # unasserted. ``suppress_selector`` (SYS-14) is defaulted so
        # this stub stays usable from any test that does not care about
        # it, but it *is* recorded below so per-state threading can be
        # asserted.
        idx = len(state["collect_calls"])
        state["collect_calls"].append(
            {
                "session": session,
                "base_path": base_path,
                "origin": origin,
                "min_depth": min_depth,
                "paginate": paginate,
                "hooks": hooks,
                "suppress_selector": suppress_selector,
            }
        )
        return state["states"][idx] if idx < len(state["states"]) else []

    async def instant_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(strategy_mod, "BrowserSession", fake_session_factory)
    monkeypatch.setattr(strategy_mod, "plausible_headless_ua", fake_ua)
    monkeypatch.setattr(strategy_mod, "collect_job_links", fake_collect)
    monkeypatch.setattr(strategy_mod.asyncio, "sleep", instant_sleep)
    return state


def _make_company(**overrides: Any) -> Company:
    """Build a minimal :class:`Company` that declares ``pre_filter_urls``."""
    defaults: dict[str, Any] = {
        "name": "Plana Tech",
        "job_board_url": "https://example.com/careers",
        "sample_job_url": "https://example.com/jobs/abc",
        "pre_filter_urls": (
            "https://example.com/careers?dept=eng",
            "https://example.com/careers?dept=ops",
        ),
    }
    defaults.update(overrides)
    return Company(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestUnionAndDedup:
    """Cross-state URL union with dedup — AC bullet 2.

    The prefiltered path collects one URL set per declared state and
    unions them; duplicate anchors that appear on multiple states must
    collapse to one entry in the final ``jobs`` list.
    """

    def test_two_state_union_dedups(
        self, ctx: RunContext, patched: dict[str, Any]
    ) -> None:
        patched["states"] = [
            ["https://example.com/jobs/a", "https://example.com/jobs/b"],
            ["https://example.com/jobs/b", "https://example.com/jobs/c"],
        ]
        company = _make_company()

        report = _run(DomStrategy()._extract_prefiltered(company, ctx))

        assert sorted(report["jobs"]) == sorted(
            {
                "https://example.com/jobs/a",
                "https://example.com/jobs/b",
                "https://example.com/jobs/c",
            }
        )
        assert report["metadata"]["states_visited"] == 2
        assert report["metadata"]["error"] is None

    def test_zero_anchor_state_is_legitimate(
        self, ctx: RunContext, patched: dict[str, Any]
    ) -> None:
        """A state contributing no anchors is not an error — AC bullet 3.

        The loop continues past an empty state, session.stop is called
        cleanly, and the report carries the surviving union from the
        other states with ``error=None``.
        """
        patched["states"] = [
            ["https://example.com/jobs/a"],
            [],
        ]
        company = _make_company()

        report = _run(DomStrategy()._extract_prefiltered(company, ctx))

        assert report["jobs"] == ["https://example.com/jobs/a"]
        assert report["metadata"]["states_visited"] == 2
        assert report["metadata"]["error"] is None
        assert patched["session"].stop_count == 1


class TestPerStateThreading:
    """Hooks + paginate propagate to every state, order preserved — AC bullet 4."""

    def test_paginate_and_hooks_thread_through(
        self, ctx: RunContext, patched: dict[str, Any]
    ) -> None:
        patched["states"] = [["x"], ["y"]]
        hooks = RuntimeHooks(pre_extract_css=".jobs{display:block}")
        company = _make_company(
            paginate=True,
            hooks=hooks,
            pre_filter_urls=(
                "https://example.com/careers?dept=eng",
                "https://example.com/careers?dept=ops",
            ),
        )

        _run(DomStrategy()._extract_prefiltered(company, ctx))

        session = patched["session"]
        assert session.start_count == 1
        assert session.stop_count == 1
        assert session.navigate_calls == [
            "https://example.com/careers?dept=eng",
            "https://example.com/careers?dept=ops",
        ]

        calls = patched["collect_calls"]
        assert len(calls) == 2
        for call in calls:
            assert call["paginate"] is True
            assert call["hooks"] is hooks
            # base_path derives from sample_job_url when LinkRule.path_prefix is None.
            assert call["base_path"] == "/jobs"
            assert call["origin"] == "https://example.com"
            assert call["min_depth"] == 1  # LinkRule default

    def test_suppress_ancestor_selector_threads_to_every_state(
        self, ctx: RunContext, patched: dict[str, Any]
    ) -> None:
        # SYS-14 threading on the SYS-13 path: the selector is a matcher
        # argument, so it must reach *every* per-state collect. A board
        # that needs container suppression needs it on all of its
        # pre-filter states, not just the first.
        selector = '[data-automation="featured-opportunities"]'
        company = _make_company(
            link_rule=LinkRule(suppress_ancestor_selector=selector),
        )

        _run(DomStrategy()._extract_prefiltered(company, ctx))

        calls = patched["collect_calls"]
        assert len(calls) == 2
        assert [c["suppress_selector"] for c in calls] == [selector, selector]

    def test_default_link_rule_passes_no_suppression(
        self, ctx: RunContext, patched: dict[str, Any]
    ) -> None:
        # The corpus default: ``None`` reaches the collector, which
        # forwards it as the matcher's fourth argument where it disables
        # the gate.
        _run(DomStrategy()._extract_prefiltered(_make_company(), ctx))

        calls = patched["collect_calls"]
        assert [c["suppress_selector"] for c in calls] == [None, None]

    def test_explicit_link_rule_supersedes_derivation(
        self, ctx: RunContext, patched: dict[str, Any]
    ) -> None:
        """An explicit ``LinkRule`` beats ``derive_path_prefix``."""
        patched["states"] = [[]]
        company = _make_company(
            link_rule=LinkRule(path_prefix="/careers", min_depth=2),
            pre_filter_urls=("https://example.com/careers?a=1",),
        )

        _run(DomStrategy()._extract_prefiltered(company, ctx))

        call = patched["collect_calls"][0]
        assert call["base_path"] == "/careers"
        assert call["min_depth"] == 2


class TestAgentLessProof:
    """Structural proof: ``build_agent`` is unreachable on the prefiltered path.

    AC bullet 1. This is the C18 structural closure. A booby-trapped ``build_agent``
    raising an :class:`AssertionError` proves the LLM entry-point was
    never touched; combined with the Task 9 live check (running the
    binary with ``OPENAI_API_KEY`` unset), this closes the "runtime is
    agent-free when declaring" invariant end-to-end.
    """

    def test_build_agent_never_called_and_report_shape(
        self,
        ctx: RunContext,
        patched: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def booby_trap(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("LLM path entered")

        monkeypatch.setattr(
            "vacantes.extraction.dom.agent.runner.build_agent", booby_trap
        )
        patched["states"] = [["https://example.com/jobs/1"]]
        company = _make_company(
            pre_filter_urls=("https://example.com/careers?a=1",),
            expected_jobs=1,
        )

        # If _extract_prefiltered ever imported/called build_agent, the
        # AssertionError would bubble out through asyncio.run.
        report = _run(DomStrategy()._extract_prefiltered(company, ctx))

        meta = report["metadata"]
        assert meta["strategy"] == "dom"
        assert meta["model"] is None
        assert meta["agent_steps"] is None
        assert meta["agent_completed"] is None
        assert meta["agent_had_errors"] is None
        assert meta["expected_jobs"] == 1
        assert meta["verdict"] == "match"
        assert meta["states_visited"] == 1
        assert meta["total_jobs_found"] == 1


class TestErrorPath:
    """Mid-run exception → error report, ``session.stop`` still runs — AC bullet 5.

    A collector raising on state 0 clears the union and folds the error
    into the report; the ``finally`` block guarantees ``session.stop()``
    is called exactly once. ``states_visited`` reflects the declared
    URL count (not the successfully-completed count) per Task 3
    design.
    """

    def test_collector_exception_folds_into_report(
        self, ctx: RunContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The ``patched`` fixture's scripted-list mechanism cannot make
        # collect_job_links raise, so this test re-installs the stubs
        # locally with a raising collector.
        state: dict[str, Any] = {"session": None}

        def fake_session_factory(*, browser_profile: Any = None) -> _StubBrowserSession:
            sess = _StubBrowserSession(browser_profile=browser_profile)
            state["session"] = sess
            return sess

        async def fake_ua() -> str:
            return "TestAgent/1.0"

        async def raising_collect(*args: Any, **kwargs: Any) -> list[str]:
            raise RuntimeError("matcher blew up")

        async def instant_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr(strategy_mod, "BrowserSession", fake_session_factory)
        monkeypatch.setattr(strategy_mod, "plausible_headless_ua", fake_ua)
        monkeypatch.setattr(strategy_mod, "collect_job_links", raising_collect)
        monkeypatch.setattr(strategy_mod.asyncio, "sleep", instant_sleep)

        company = _make_company(
            pre_filter_urls=(
                "https://example.com/careers?a=1",
                "https://example.com/careers?a=2",
            ),
        )

        report = _run(DomStrategy()._extract_prefiltered(company, ctx))

        # Union cleared, error string surfaced, session stopped once.
        assert report["jobs"] == []
        assert report["metadata"]["error"] == "matcher blew up"
        assert report["metadata"]["total_jobs_found"] == 0
        # states_visited reflects the declared count, not the completed count.
        assert report["metadata"]["states_visited"] == 2
        assert state["session"].stop_count == 1


class TestReportShapeByteStability:
    """The ``states_visited`` key is absent when the caller omits the kwarg.

    Byte-stability guard on :func:`build_report`: pre-SYS-13 callers
    (agent DOM path, Greenhouse strategy, any future caller that does
    not opt in) pass ``None`` as the default and must produce a
    ``metadata`` dict without the key at all — not present-with-null.
    This is the load-bearing property honoured by JSON serialisation,
    ``print_summary``, and ``--strict``.
    """

    def test_states_visited_absent_when_kwarg_omitted(self) -> None:
        from vacantes.extraction.base import build_report

        report = build_report(
            strategy="dom",
            company_name="Legacy Agent Co",
            company_url="https://example.com/careers",
            jobs=["https://example.com/jobs/1"],
            elapsed=1.23,
            model="gpt-4.1-mini",
            agent_steps=5,
            agent_completed=True,
            agent_had_errors=False,
            error=None,
            expected_jobs=1,
        )

        assert "states_visited" not in report["metadata"]

    def test_states_visited_present_when_kwarg_supplied(self) -> None:
        from vacantes.extraction.base import build_report

        report = build_report(
            strategy="dom",
            company_name="Prefiltered Co",
            company_url="https://example.com/careers",
            jobs=[],
            elapsed=0.5,
            model=None,
            agent_steps=None,
            agent_completed=None,
            agent_had_errors=None,
            error=None,
            expected_jobs=None,
            states_visited=3,
        )

        assert report["metadata"]["states_visited"] == 3
