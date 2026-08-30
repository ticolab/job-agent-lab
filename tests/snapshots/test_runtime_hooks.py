"""Collector-level hook-mechanic pins against real Chromium (SYS-12).

This module is the canonical file for the *joint* SYS-12 hook mechanics
— behaviours that involve two or more of :func:`apply_pre_extract_css`,
:func:`expand_all`, ``walk_and_collect``'s ``next_control_override``,
and the matcher itself running together on the same DOM. It complements
two sibling files that pin the primitives in isolation:

- ``test_hook_execution.py`` (Task 2) — helper-primitive coverage for
  ``apply_pre_extract_css`` and ``expand_all`` in isolation: valid-CSS
  rule count, idempotent replacement, invalid-payload loud raise, the
  whitespace edge, single-round expansion happy path, the visibility
  gate, zero-match early exit, the ``max_rounds`` cap, and per-click
  exception swallow.
- ``test_pagination_walker.py`` (Task 3) — ``walk_and_collect``'s
  ``next_control_override`` in three configurations: override wins over
  a signal-cascade decoy, override left unset regresses to the
  six-signal cascade, and override matching nothing terminates at
  state 1 with no cascade fallback.

The four scenarios pinned here are the ones that only arise when hooks
compose (multi-round expansion where round-1 clicks mount round-2
triggers, an anchor becoming matcher-visible only after CSS injection,
the §4.5 order dependency between css/expand/matcher, and the SPA
persistence guarantee that a single ``pre_extract_css`` injection
survives a walker state transition).

The coroutine-drive pattern, JS-enabled context construction, and
sync-Playwright shim mirror ``test_hook_execution.py`` and
``test_pagination_walker.py`` — see the docstring in
``test_pagination_walker.py`` for the rationale on why
``asyncio.run`` is bypassed and why the fixtures monkeypatch
``collector_mod.asyncio.sleep``. No LLM, no network.
"""

from __future__ import annotations

import time
from collections.abc import Coroutine, Iterable
from pathlib import Path
from typing import Any, cast

import pytest
from playwright.sync_api import Browser, BrowserContext, Page

from job_agent_lab.extraction.dom import collector as collector_mod
from job_agent_lab.extraction.dom.collector import (
    ActorPageDriver,
    _run_matcher,
    apply_pre_extract_css,
    expand_all,
    walk_and_collect,
)

_PREFIX = "/jobs"


# ---------------------------------------------------------------------------
# Manual coroutine driver
# ---------------------------------------------------------------------------


def _drive(coro: Coroutine[Any, Any, Any]) -> Any:
    """Drive ``coro`` to completion without any real event loop.

    Every ``await`` in the helpers-under-test resolves synchronously
    (see :func:`~test_pagination_walker._drive`'s docstring for the
    full argument). Yielding to a scheduler here would mean a new
    unmocked async primitive slipped in — we raise loudly rather than
    hang so the drift is diagnosable at test-review time.
    """
    try:
        yielded = coro.send(None)
    except StopIteration as stop:
        return stop.value

    raise AssertionError(
        "hook coroutine yielded to a scheduler "
        f"(value={yielded!r}); did fast_hooks fail to monkeypatch "
        "asyncio.sleep, or did a helper add a new await point?"
    )


# ---------------------------------------------------------------------------
# Driver shim
# ---------------------------------------------------------------------------


class _HookTestDriver:
    """Async ``PageDriver`` shim over a sync Playwright ``Page``.

    Mirrors ``test_hook_execution._HookTestDriver`` — the async methods
    forward directly to blocking sync-Playwright calls with no inner
    ``await``, so ``_drive`` completes each via a single
    ``StopIteration``. Click carries a short timeout so a missing
    selector fails fast rather than blocking the test on Playwright's
    30 s auto-wait default.
    """

    def __init__(self, page: Page) -> None:
        self._page = page

    async def evaluate(self, js: str, arg: Any) -> Any:
        return self._page.evaluate(js, arg)

    async def click(self, selector: str) -> None:
        self._page.click(selector, timeout=1000)

    async def url(self) -> str:
        result: str = self._page.url
        return result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def js_context(browser: Browser) -> Iterable[BrowserContext]:
    """A JS-enabled context; the snapshot conftest's JS-off is bypassed.

    ``tests/snapshots/conftest.py`` disables page JS at every auto-
    created context so snapshot ``<script>`` tags cannot re-hydrate
    the DOM at test time. The hook mechanics under test rely on real
    click handlers (chained accordion mounts), the real CSS parser
    (SentinelOne-shape unhide), and a real JS-driven SPA state swap
    (walker persistence). Constructing an explicit JS-enabled context
    bypasses the file-scope override without touching the harness's
    default.
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
def fast_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink both the walker and expansion settle constants.

    Production defaults are tuned for real SPAs — 0.5 s poll, 10 s
    settle ceiling, 1 s mount grace, 0.7 s expand settle. Synthetic
    ``file://`` fixtures mount synchronously and would eat the full
    budget for no gain, so we drop them to millisecond-scale values.
    Replacing ``asyncio.sleep`` with a synchronous fake keeps
    :func:`_drive` from encountering a scheduler yield; real
    wallclock still advances via ``time.sleep`` so the settle-timeout
    branch continues to work.
    """
    monkeypatch.setattr(collector_mod, "POLL_INTERVAL_SEC", 0.02)
    monkeypatch.setattr(collector_mod, "SETTLE_TIMEOUT_SEC", 1.0)
    monkeypatch.setattr(collector_mod, "MOUNT_GRACE_SEC", 0.02)
    monkeypatch.setattr(collector_mod, "EXPAND_SETTLE_SEC", 0.01)

    async def _sync_sleep(seconds: float) -> None:
        time.sleep(seconds)

    monkeypatch.setattr(collector_mod.asyncio, "sleep", _sync_sleep)


# ---------------------------------------------------------------------------
# Matcher helper — probes the page after hooks fire
# ---------------------------------------------------------------------------


def _matcher_urls(page: Page, driver: _HookTestDriver) -> set[str]:
    """Run the production matcher against the driver's current DOM state.

    The matcher's origin gate is a strict-equality check against the
    ``origin`` argument. Chromium reports ``URL.origin`` as the opaque
    string ``"null"`` for ``file://`` URLs, so we read the observed
    origin from the loaded page rather than hardcode a mismatched value
    that would silently drop every anchor.
    """
    origin: str = cast(str, page.evaluate("() => new URL(location.href).origin"))
    urls = _drive(_run_matcher(driver, _PREFIX, origin, 1))
    return set(urls)


# ---------------------------------------------------------------------------
# Test 1: multi-round expansion (round-1 click mounts round-2 triggers)
# ---------------------------------------------------------------------------


_MULTI_ROUND_PAGE = """<!DOCTYPE html>
<html>
<head>
  <base href="file:///">
  <style>
    .child { display: none; }
    .child.open { display: block; }
    /* Round-2 trigger starts absent from the DOM entirely — it is
       *appended* by the round-1 click handler, so the first stamp
       query cannot see it. */
  </style>
</head>
<body>
<button class="trigger" id="outer">Show outer</button>
<div class="child" id="outer-payload">
  <!-- Inner trigger is appended here on outer click. -->
</div>
<script>
document.getElementById('outer').addEventListener('click', () => {
  const target = document.getElementById('outer-payload');
  target.classList.add('open');
  // Mount a new .trigger inside the just-opened child. Its own click
  // reveals the anchor. The outer button is stripped of its .trigger
  // class so the round-2 stamp query only sees the inner one.
  const inner = document.createElement('button');
  inner.className = 'trigger';
  inner.id = 'inner';
  inner.textContent = 'Show inner';
  inner.addEventListener('click', () => {
    const link = document.createElement('a');
    link.href = '/jobs/deep-role';
    link.textContent = 'Deep role';
    target.appendChild(link);
    inner.classList.remove('trigger');
  });
  target.appendChild(inner);
  document.getElementById('outer').classList.remove('trigger');
});
</script>
</body>
</html>"""


class TestExpansionMultiRoundMount:
    """Round-1 clicks mount round-2 triggers; matcher sees mounted anchor.

    Real accordions do not always flatten in a single round — a
    top-level "Locations" accordion may mount per-region children whose
    expansion in turn mounts individual role rows. The expander's
    re-query-per-round shape must handle this: round 1 sees only the
    outer trigger and clicks it; round 2 sees the freshly-mounted inner
    trigger and clicks it; round 3 sees nothing and terminates. Only
    after those two rounds is the target anchor in the DOM.
    """

    def test_two_rounds_reveal_mounted_anchor(
        self, tmp_path: Path, js_page: Page, fast_hooks: None
    ) -> None:
        p = tmp_path / "multi.html"
        p.write_text(_MULTI_ROUND_PAGE, encoding="utf-8")
        js_page.goto(p.as_uri())
        driver = _HookTestDriver(js_page)

        # Before expansion: no anchor exists yet.
        before = _matcher_urls(js_page, driver)
        assert before == set()

        total_clicks = _drive(expand_all(driver, ".trigger"))

        # Exactly two clicks: round-1 outer, round-2 inner. Round-3
        # stamps zero and terminates the loop.
        assert total_clicks == 2

        after = _matcher_urls(js_page, driver)
        assert after == {"file:///jobs/deep-role"}


# ---------------------------------------------------------------------------
# Test 2: CSS unhide flips anchor from matcher-invisible to matcher-visible
# ---------------------------------------------------------------------------


_CSS_HIDDEN_PAGE = """<!DOCTYPE html>
<html>
<head>
  <base href="file:///">
  <style>
    /* SentinelOne-shape: anchors carry a Tailwind-style responsive
       hidden class that resolves to display: none until a media-query
       override fires. The matcher's visibility gate rejects them at
       load time. A pre-extract CSS injection with the corresponding
       display:block !important rule flips them visible. */
    .md\\:hidden { display: none; }
  </style>
</head>
<body>
<a class="md:hidden" href="/jobs/hidden-a">Hidden A</a>
<a class="md:hidden" href="/jobs/hidden-b">Hidden B</a>
<a href="/jobs/visible-c">Visible C</a>
</body>
</html>"""


class TestCssUnhideAnchor:
    """CSS injection flips anchors from matcher-hidden to matcher-visible.

    Motivating case: a board (SentinelOne shape in the ticket) hides
    listing anchors behind a responsive utility class that resolves to
    ``display: none`` at the viewport size the runtime uses. The
    matcher's ``checkVisibility({checkVisibilityCSS: true})`` gate
    drops them, producing a zero count. Injecting a
    ``.md\\:hidden { display: block !important; }`` payload flips
    every affected anchor visible; the very next matcher pass sees
    the full set.
    """

    def test_hidden_anchors_become_visible_after_inject(
        self, tmp_path: Path, js_page: Page, fast_hooks: None
    ) -> None:
        p = tmp_path / "css-hidden.html"
        p.write_text(_CSS_HIDDEN_PAGE, encoding="utf-8")
        js_page.goto(p.as_uri())
        driver = _HookTestDriver(js_page)

        # Before injection: only the always-visible anchor is collected.
        # The two ``md:hidden`` anchors fail the visibility gate.
        before = _matcher_urls(js_page, driver)
        assert before == {"file:///jobs/visible-c"}

        rule_count = _drive(
            apply_pre_extract_css(driver, r".md\:hidden { display: block !important; }")
        )
        assert rule_count == 1

        # After injection: all three anchors are visible to the matcher.
        after = _matcher_urls(js_page, driver)
        assert after == {
            "file:///jobs/hidden-a",
            "file:///jobs/hidden-b",
            "file:///jobs/visible-c",
        }


# ---------------------------------------------------------------------------
# Test 3: §4.5 order dependency (css → expand → matcher)
# ---------------------------------------------------------------------------


_ORDER_DEPENDENT_PAGE = """<!DOCTYPE html>
<html>
<head>
  <base href="file:///">
  <style>
    /* The accordion header itself is CSS-hidden by a class. Without a
       pre-extract CSS unhide, the expander's visibility gate skips it
       and its child anchors are never mounted. Order matters:
       CSS-inject must precede expansion. */
    .cloaked { display: none; }
    .payload { display: none; }
    .payload.open { display: block; }
  </style>
</head>
<body>
<button class="trigger cloaked" id="hdr">Show roles</button>
<div class="payload" id="pl">
  <a href="/jobs/role-1">Role 1</a>
  <a href="/jobs/role-2">Role 2</a>
</div>
<script>
document.getElementById('hdr').addEventListener('click', () => {
  document.getElementById('pl').classList.add('open');
  // Strip the ``.trigger`` class so the next stamp round no longer
  // matches this element — otherwise ``expand_all`` re-clicks up to
  // its ``max_rounds`` cap. This mirrors the accordion-shape
  // convention pinned by the ``_EXPAND_PAGE_TEMPLATE`` fixture in
  // ``test_hook_execution.py``.
  document.getElementById('hdr').classList.remove('trigger');
});
</script>
</body>
</html>"""


class TestIntegrationOrder:
    """CSS injection must precede expansion, expansion must precede matcher.

    Pins the §4.5 execution order (documented in
    ``ARCHITECTURE_PROPOSAL_R2.md`` and mirrored in
    :func:`collect_job_links`): if expansion runs before the CSS
    unhide, the accordion trigger is invisible and the expander's
    visibility gate skips it, so its child anchors never mount and the
    matcher returns an empty set. Running the three helpers in the
    pinned order — css → expand → collect — recovers the full anchor
    set.

    This test drives the helpers directly rather than through
    :func:`collect_job_links` so the ordering is visible in the test
    body itself; :func:`collect_job_links`'s inert-hooks bypass and
    ``ActorPageDriver`` construction are exercised by the strategy
    port's own registry and completeness tests.
    """

    def test_order_css_then_expand_then_collect(
        self, tmp_path: Path, js_page: Page, fast_hooks: None
    ) -> None:
        p = tmp_path / "order.html"
        p.write_text(_ORDER_DEPENDENT_PAGE, encoding="utf-8")
        js_page.goto(p.as_uri())
        driver = _HookTestDriver(js_page)

        # Step 1 (CSS): unhide the ``.cloaked`` trigger. Without this,
        # step 2 sees no visible ``.trigger`` and stamps zero.
        _drive(apply_pre_extract_css(driver, ".cloaked { display: block; }"))

        # Step 2 (expand): the now-visible trigger fires once; the
        # child ``.payload`` becomes visible, exposing its two anchors
        # to the matcher.
        total_clicks = _drive(expand_all(driver, ".trigger"))
        assert total_clicks == 1

        # Step 3 (matcher): both child anchors are collected.
        urls = _matcher_urls(js_page, driver)
        assert urls == {"file:///jobs/role-1", "file:///jobs/role-2"}

    def test_wrong_order_produces_empty_set(
        self, tmp_path: Path, js_page: Page, fast_hooks: None
    ) -> None:
        """Regression guard on the order: expand-before-css collects nothing.

        Not a supported code path — :func:`collect_job_links` always
        fires css first — but pinning the failure mode makes the order
        dependency observable in the test suite rather than an
        implicit invariant.
        """
        p = tmp_path / "order-neg.html"
        p.write_text(_ORDER_DEPENDENT_PAGE, encoding="utf-8")
        js_page.goto(p.as_uri())
        driver = _HookTestDriver(js_page)

        # Expand first (CSS not yet injected → trigger is cloaked →
        # stamp count is zero → no clicks fire).
        total_clicks = _drive(expand_all(driver, ".trigger"))
        assert total_clicks == 0

        # Matcher then sees an empty set (payload never opened).
        urls = _matcher_urls(js_page, driver)
        assert urls == set()


# ---------------------------------------------------------------------------
# Test 4: combined hooks across paginated states (CSS persistence)
# ---------------------------------------------------------------------------


_SPA_PAGINATED_PAGE = """<!DOCTYPE html>
<html>
<head>
  <base href="file:///">
  <style>
    /* Both state 1 and state 2 anchors carry a CSS-hidden class. A
       one-time pre-extract CSS injection must survive the SPA state
       swap so state 2's anchors are matcher-visible after the walker
       advances. */
    .hidden-anchor { display: none; }
    /* State-scoped visibility: only the current state's anchor
       container is display: block. The SPA advance toggles the
       classes on click — no hard navigation, so the injected
       <style data-jal-css> element persists in <head>. */
    .state { display: none; }
    .state.active { display: block; }
  </style>
</head>
<body>
<div class="state active" id="state-1">
  <a class="hidden-anchor" href="/jobs/s1-a">S1 A</a>
  <a class="hidden-anchor" href="/jobs/s1-b">S1 B</a>
</div>
<div class="state" id="state-2">
  <a class="hidden-anchor" href="/jobs/s2-c">S2 C</a>
</div>
<a data-role="pager" href="javascript:void(0)" id="pager">Next</a>
<script>
// The pager click swaps active states in-place — the document
// object is preserved across the state transition, exactly the
// Techwarely / BCG shape the walker's PageDriver.click path is
// designed for. No document reload means the injected
// <style data-jal-css> survives.
document.getElementById('pager').addEventListener('click', (e) => {
  e.preventDefault();
  document.getElementById('state-1').classList.remove('active');
  document.getElementById('state-2').classList.add('active');
  // Remove the pager on state 2 so the walker terminates cleanly on
  // the next discovery pass (override selector no longer matches).
  e.currentTarget.remove();
});
</script>
</body>
</html>"""


class TestCombinedHooksAcrossStates:
    """CSS injection survives an SPA walker advance; walker override drives it.

    Pins the SPA-persistence half of the Approach section's documented
    limitation on pre-extract CSS: the injected ``<style data-jal-css>``
    element survives any state transition that preserves the document
    (JS-driven ``classList`` swaps, ``history.pushState``, hashchange
    handlers) because those transitions never rebuild ``<head>``.
    Concretely: state 1 has two CSS-hidden anchors, state 2 has one
    CSS-hidden anchor, and both are collected in the walker's union so
    long as the CSS was injected *before* the walker started.

    Simultaneously exercises the ``next_control_selector`` override —
    the pager here has no href advance and no signal-cascade-winning
    label (a bare "Next" would be signal 3, but a ``data-role="pager"``
    selector target is arbitrary). Without the override the walker
    would still find the "Next" text and try to click, which happens
    to work here — so the fixture uses the override to make the
    intent explicit and future-proof against the fixture drifting.
    """

    def test_css_persists_across_spa_state_and_override_drives_walk(
        self, tmp_path: Path, js_page: Page, fast_hooks: None
    ) -> None:
        p = tmp_path / "spa.html"
        p.write_text(_SPA_PAGINATED_PAGE, encoding="utf-8")
        js_page.goto(p.as_uri())
        driver = _HookTestDriver(js_page)

        # Sanity: without CSS unhide, matcher sees zero anchors on
        # state 1 (both are ``display: none`` inheriting from the
        # active container's default of block, then class-gated to
        # ``.hidden-anchor { display: none }``).
        before = _matcher_urls(js_page, driver)
        assert before == set()

        # Inject once, before the walker runs. The class-gated
        # ``display: none`` is overridden by ``display: block
        # !important``.
        _drive(
            apply_pre_extract_css(
                driver, ".hidden-anchor { display: block !important; }"
            )
        )

        # State 1's two anchors are now visible.
        state_1_urls = _matcher_urls(js_page, driver)
        assert state_1_urls == {"file:///jobs/s1-a", "file:///jobs/s1-b"}

        # Walker advances via the override; the CSS survives the swap.
        origin: str = cast(str, js_page.evaluate("() => new URL(location.href).origin"))
        collected = _drive(
            walk_and_collect(
                driver,
                _PREFIX,
                origin,
                1,
                next_control_override="a[data-role=pager]",
            )
        )

        # Union includes state 2's anchor — proves the injected style
        # element survived the SPA state transition and unhid the
        # state-2 anchor for the state-2 matcher pass.
        assert collected == {
            "file:///jobs/s1-a",
            "file:///jobs/s1-b",
            "file:///jobs/s2-c",
        }

        # Belt-and-braces: assert the ``<style data-jal-css>`` element
        # is still in the DOM after the walker completed. If the SPA
        # advance had blown it away (hard nav), it would be gone.
        style_count = js_page.evaluate(
            "() => document.head.querySelectorAll('style[data-jal-css]').length"
        )
        assert style_count == 1


# ---------------------------------------------------------------------------
# ActorPageDriver import guard
# ---------------------------------------------------------------------------
#
# ``ActorPageDriver`` is imported at module top so a future refactor that
# renames or removes it fails this file's import phase rather than at the
# runtime callsite. It is not exercised by any test here (the shim above
# stands in for its runtime role); the import guard is deliberate.
_ = ActorPageDriver
