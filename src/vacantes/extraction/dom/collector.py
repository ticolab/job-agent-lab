"""Runtime glue that executes the DOM matcher against a live page.

``collect_job_links`` is the thin ``page.evaluate`` wrapper that runs
``EXTRACT_JOB_LINKS_JS`` (loaded from the packaged asset via
``extraction.dom.__init__``) against a browser-use ``BrowserSession``'s
current page. The matcher itself is data (the ``.js`` file); this module
owns the Python-side call convention: pass ``[base_path, origin]`` as the
JS arg, tolerate the double-encoded-string return shape by re-parsing it,
and normalise ``None`` / missing results to an empty list.

Under matcher v2 (SYS-2) the frame / shadow-DOM walk runs **inside** the
JS asset, in the top frame's execution context, so this module deliberately
performs no driver-side frame iteration and does not touch ``page.frames``.
Cross-origin frames are structurally unreachable from an in-page walk and
therefore contribute zero links here; reaching them would require a
driver-side collector and a different browser-use page abstraction than
the runtime currently exposes.

Separated from ``extraction.dom.agent.controller`` so the controller's registered
``extract_job_links`` tool has no Playwright/JSON handling in it — the
tool just calls ``collect_job_links`` and packages the result as an
``ActionResult``. That keeps the browser-use integration and the DOM
work independently testable.

SYS-5 adds the pagination walker. When ``collect_job_links`` is invoked
with ``paginate=True`` (threaded from the ``Company.paginate`` flag), it
delegates to :func:`walk_and_collect`, which composes discovery
(``FIND_NEXT_CONTROL_JS``), a driver-side click on the stamped
``[data-jal-next]`` marker, a poll-for-change settle phase, and a
per-state matcher run whose href sets are unioned across states. The
default ``paginate=False`` path is byte-identical to the pre-SYS-5
single-shot behaviour and remains unchanged. The three surfaces that
consume the walker (runtime, capture script, tests) each supply their
own :class:`PageDriver` adapter: the runtime uses :class:`ActorPageDriver`
over the browser-use page handle; the capture script provides a
Playwright adapter; tests use whichever fits their fixtures. This keeps
the walker loop itself directly unit-testable and never reimplemented.

SYS-12 adds the deterministic hook phase between agent handoff and
matcher invocation. When ``collect_job_links`` is invoked with a
non-inert :class:`~vacantes.domain.company.RuntimeHooks` (or with
``paginate=True``), an :class:`ActorPageDriver` is built once and the
per-board hooks fire in a fixed order, pinned by ``TestIntegrationOrder``
in ``tests/snapshots/test_runtime_hooks.py``: :func:`apply_pre_extract_css`
injects a ``<style data-jal-css>`` element in the top document
(idempotent — re-running replaces the element; invalid payloads that
parse to zero rules raise loudly, never a silent no-op), then
:func:`expand_all` runs bounded click-all rounds against
``hooks.expand_selector`` (per round: stamp every *visible* match with
an indexed ``data-jal-expand`` marker, click each stamp via the
:class:`PageDriver` seam swallowing per-click failures, clear the
markers, sleep :data:`EXPAND_SETTLE_SEC`, re-query — terminate on a
zero-stamp round or at the ``max_rounds`` cap), then the single-shot
matcher or :func:`walk_and_collect` runs against the prepared DOM. The
inert-hooks + ``paginate=False`` path constructs no driver and stays
byte-identical to the pre-SYS-12 shape.

Two scope limitations apply to the injected stylesheet. First, it lives
in the top document's ``<head>`` — light DOM only, matching the walker
and expander. Second, it survives SPA-style pagination where the
document persists across states (Techwarely, BCG) but is blown away by
a hard navigation between states, re-hiding CSS-gated anchors on
state 2+; no current board combines ``pre_extract_css`` with hard-nav
pagination. Expansion likewise runs against state 1 only.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any, Protocol

from browser_use.browser.session import BrowserSession

from vacantes.domain.company import RuntimeHooks
from vacantes.extraction.dom import (
    EXTRACT_JOB_LINKS_JS,
    FIND_NEXT_CONTROL_JS,
)

# ---------------------------------------------------------------------------
# Walker tuning constants
#
# These are module-level so tests can monkeypatch them to shrink real-time
# waits and capture callers (SYS-5 Task 4) can import ``MAX_PAGES`` as the
# single source of truth for the cap. The walker reads each constant at
# call time (not at function-definition time) so a ``monkeypatch.setattr``
# is honoured for the current test.
# ---------------------------------------------------------------------------

# Hard cap on the number of DOM states the walker will collect from a
# single :func:`walk_and_collect` invocation. Motivated by the two live
# boards SYS-5 targets (Techwarely 2 pages, BCG 2 pages) plus generous
# headroom; a legitimate board exceeding this cap should raise the
# constant with evidence, per the ticket's open item.
MAX_PAGES: int = 20

# Seconds between per-state matcher probes while waiting for a click to
# take effect. Kept short so a fast SPA advances the walker promptly.
POLL_INTERVAL_SEC: float = 0.5

# Upper bound on the total time the walker will wait for the collected
# URL set to differ from the pre-click set. If exceeded, the walker
# terminates with the states it has already collected — the click is
# treated as a no-op.
SETTLE_TIMEOUT_SEC: float = 10.0

# After the settle poll sees a diff, wait this long before the final
# collect. This absorbs the tail of a mount animation / lazy hydration
# so the *complete* new state is measured rather than the transitional
# partial state.
MOUNT_GRACE_SEC: float = 1.0

# The marker attribute the discovery JS stamps on the winning next-page
# control. Kept as a module-level constant (rather than inlined) so the
# walker's click selector and any diagnostic code reference the same
# literal the JS asset uses. Any rename must change both surfaces.
_NEXT_MARKER_SELECTOR: str = "[data-jal-next]"


# ---------------------------------------------------------------------------
# Expansion tuning constants (SYS-12)
#
# Read at call time from :func:`expand_all` so a ``monkeypatch.setattr``
# on this module honours the change for the current test (mirrors the
# walker's ``POLL_INTERVAL_SEC`` precedent). The *cap* is bound as a
# default keyword argument on :func:`expand_all` and is therefore NOT
# monkeypatchable — pass ``max_rounds=`` explicitly in tests that need a
# tighter cap.
# ---------------------------------------------------------------------------

# Hard cap on the number of stamp/click rounds :func:`expand_all` will
# run against a single ``expand_selector`` invocation. Motivated by the
# two live boards SYS-12 targets whose accordion trees flatten inside
# 1–2 rounds (Deel role tiles, Progress "Show more"); five gives
# comfortable headroom for a plausible nested-accordion board that
# arrives later.
EXPAND_MAX_ROUNDS: int = 5

# Seconds to wait between a click-all round completing and the next
# stamp query. Absorbs the tail of the SPA's expand animation / lazy-
# mount so newly-revealed children are queryable before the next
# stamp fires. Kept short to keep the total prep budget bounded.
EXPAND_SETTLE_SEC: float = 0.7

# Seconds to wait after the pre-extract stylesheet is appended, before
# the caller reads visibility off the DOM. When the property the payload
# overrides is under a CSS ``transition`` (Accenture's accordion wrapper
# declares ``transition: visibility 0.55s ...``), the new value is not
# observable in the same tick: the transition only *starts* at the
# browser's next rendering update, and until then ``getComputedStyle``
# and ``checkVisibility`` keep reporting the pre-injection value — a
# forced synchronous reflow does not help, measured. Once it starts,
# ``visibility`` hidden->visible interpolates to ``visible`` for the
# whole run, so the flip lands one frame after injection (~16-50 ms
# measured), not after the declared duration; 0.3 s is generous margin.
# Known limit: a long ``transition-delay`` on the gating property would
# outlast this — raise the constant if such a board appears. Read at
# call time like the walker's constants so a ``monkeypatch.setattr`` on
# this module is honoured.
CSS_SETTLE_SEC: float = 0.3

# Attribute name stamped on visible expand targets each round. Indexed
# per round (``data-jal-expand="0"``, ``"1"``, …) so each click selects
# a stable single element even when the round mutates DOM order.
# Different from :data:`_NEXT_MARKER_SELECTOR` so the two systems can
# coexist under ``paginate=True`` + non-inert hooks.
_EXPAND_MARKER: str = "data-jal-expand"

# JS body: idempotently inject a ``<style data-jal-css>`` element in the
# top document's ``<head>``. If a prior element with the marker exists
# it is removed and replaced (re-running an inject is a no-op relative
# to the payload). Rule-count is checked against the parsed
# ``CSSStyleSheet`` — a non-whitespace payload that yields zero rules is
# a configuration error and throws loudly, never silently accepted as a
# no-op. Whitespace-only payloads (``""``, ``"  "``) return 0 without
# raising, mirroring the inert-selector edge case for expansion.
_INJECT_CSS_JS: str = """
(css) => {
  const head = document.head;
  if (!head) {
    throw new Error('apply_pre_extract_css: document has no <head>');
  }
  const existing = head.querySelector('style[data-jal-css]');
  if (existing) existing.remove();
  const styleEl = document.createElement('style');
  styleEl.setAttribute('data-jal-css', '');
  styleEl.textContent = css;
  head.appendChild(styleEl);
  const sheet = styleEl.sheet;
  const ruleCount = sheet ? sheet.cssRules.length : 0;
  if (css.trim().length > 0 && ruleCount === 0) {
    throw new Error(
      'apply_pre_extract_css: payload parsed to zero CSS rules: ' +
      css.slice(0, 200)
    );
  }
  return ruleCount;
}
"""

# JS body: clear any stale ``data-jal-expand`` markers in the top
# document, then query the caller-supplied selector, filter to
# CSS-visible matches (``checkVisibility({checkVisibilityCSS: true})``
# with the same fallback shape the matcher uses), and stamp each with
# an indexed ``data-jal-expand="<k>"`` attribute. Returns the number of
# elements stamped so :func:`expand_all` knows both the round's
# click-count and whether to terminate. Light-DOM only — same-origin
# frames and shadow roots are out of scope for the expander (see the
# module docstring).
_STAMP_EXPAND_TARGETS_JS: str = """
([selector, marker]) => {
  document.querySelectorAll('[' + marker + ']').forEach(el => {
    el.removeAttribute(marker);
  });
  const isVisible = (el) => {
    if (typeof el.checkVisibility === 'function') {
      try { return el.checkVisibility({ checkVisibilityCSS: true }); }
      catch (_) { /* fall through to offsetParent fallback */ }
    }
    if (el.offsetParent !== null) return true;
    const cs = window.getComputedStyle(el);
    return cs.position === 'fixed'
      && cs.display !== 'none'
      && cs.visibility !== 'hidden';
  };
  const visible = Array.from(document.querySelectorAll(selector))
    .filter(isVisible);
  visible.forEach((el, i) => { el.setAttribute(marker, String(i)); });
  return visible.length;
}
"""

# JS body: clear every ``data-jal-expand`` marker in the top document.
# Called at the end of each expansion round so the next round's stamp
# starts from a clean slate (the stamp JS also clears defensively, but
# an explicit clear between rounds keeps the DOM tidy during the
# settle sleep in case in-flight code inspects the attribute).
_CLEAR_EXPAND_MARKERS_JS: str = """
(marker) => {
  const nodes = document.querySelectorAll('[' + marker + ']');
  nodes.forEach(el => { el.removeAttribute(marker); });
  return nodes.length;
}
"""


# ---------------------------------------------------------------------------
# PageDriver abstraction
#
# The walker is defined against a 3-method structural protocol rather
# than the concrete browser-use ``Page``. Three consumers implement it:
#
#   1. :class:`ActorPageDriver` (below) — the runtime adapter used
#      inside ``collect_job_links``.
#   2. The capture script (SYS-5 Task 4) — a small Playwright adapter
#      the ``scripts/capture_snapshot.py`` module defines locally.
#   3. Walker tests (SYS-5 Task 3) — a test-local adapter over an
#      explicit JS-enabled Playwright context.
#
# The protocol is deliberately narrow: everything the loop needs and
# nothing more, so each adapter is a handful of lines.
# ---------------------------------------------------------------------------


class PageDriver(Protocol):
    """Minimal browser-page surface the walker depends on."""

    async def evaluate(self, js: str, arg: Any) -> Any:
        """Execute ``js`` (a ``(...) => {...}`` arrow function body) with ``arg``.

        Return the JS function's return value with **primitives, dicts,
        and lists already parsed** — adapters over drivers that JSON-
        stringify complex return values (browser-use does) must undo
        that serialisation inside this method.
        """
        ...

    async def click(self, selector: str) -> None:
        """Click the first element matching ``selector``, driver-side.

        Adapters fire the click through the JS ``HTMLElement.click()``
        method (typically via ``page.evaluate``) rather than through a
        CDP mouse-event dispatch: on the browser-use driver the CDP
        path empirically does not propagate to anchor-navigation
        pagination controls, whereas ``element.click()`` reliably
        triggers the browser's built-in click behaviour (including
        anchor navigation and same-page SPA handlers).

        The JS-side click fires with ``event.isTrusted === false``.
        Pagination controls that specifically gate on ``isTrusted``
        would need a driver extension; no such board has been observed
        (see C7 in ``blockers/INTEGRATION_BLOCKERS.md`` for the class).

        Adapters must raise when no element matches ``selector``.
        """
        ...

    async def url(self) -> str:
        """Return the page's current URL."""
        ...


class ActorPageDriver:
    """Adapter that satisfies :class:`PageDriver` over a browser-use page handle.

    The browser-use ``Page.evaluate`` returns the JS value as a *string*
    (primitive values are stringified; dicts and lists are JSON-
    encoded). This adapter parses the JSON back into Python primitives
    so the walker's protocol contract (dicts stay dicts, lists stay
    lists) is honoured.
    """

    def __init__(self, page: Any) -> None:
        self._page = page

    async def evaluate(self, js: str, arg: Any) -> Any:
        raw = await self._page.evaluate(js, arg)
        # browser-use returns a str; playwright-async returns the parsed
        # value. Handle both defensively — a JSON-parseable str is
        # decoded, a non-JSON str is returned as-is (used for scalar
        # returns), and anything already-parsed is passed through.
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except (ValueError, TypeError):
                return raw
        return raw

    async def click(self, selector: str) -> None:
        # JS ``HTMLElement.click()`` fires the browser's built-in click
        # behaviour on the actor page. See :meth:`PageDriver.click` for
        # why this is preferred over browser-use's CDP-driven
        # ``Element.click`` on the runtime path.
        await self._page.evaluate(
            "(sel) => {"
            " const el = document.querySelector(sel);"
            " if (!el) { throw new Error("
            "'ActorPageDriver.click: no element matched selector ' + sel"
            "); }"
            " el.click();"
            "}",
            selector,
        )

    async def url(self) -> str:
        result: str = await self._page.get_url()
        return result


# ---------------------------------------------------------------------------
# Walker
# ---------------------------------------------------------------------------


async def _run_matcher(
    driver: PageDriver,
    base_path: str,
    origin: str,
    min_depth: int,
    suppress_selector: str | None = None,
) -> list[str]:
    """Run the matcher once against the driver's current DOM state.

    ``suppress_selector`` (SYS-14) mirrors
    ``Company.link_rule.suppress_ancestor_selector`` and is passed as the
    matcher's fourth argument; ``None`` disables the gate. Every walker
    invocation (state-1 collect, settle probe, per-state collect) routes
    through here, so threading it once covers the whole walk.
    """
    result = await driver.evaluate(
        EXTRACT_JOB_LINKS_JS, [base_path, origin, min_depth, suppress_selector]
    )
    # ``ActorPageDriver.evaluate`` already parses JSON, but a defensive
    # fallback keeps the walker robust against drivers that leave
    # already-parsed strings intact.
    if isinstance(result, str):
        result = json.loads(result)
    return list(result or [])


async def _find_next(
    driver: PageDriver,
    *,
    dry_run: bool = False,
    override: str | None = None,
) -> dict[str, Any]:
    """Run the discovery pass and return its ``{found, signal, text}`` dict.

    When ``override`` is a non-``None`` string, the JS asset bypasses
    its six-signal cascade and stamps ``document.querySelector(override)``
    directly (SYS-12 ``next_control_selector`` hook). A ``None`` value
    (the default) runs the normal cascade.
    """
    result = await driver.evaluate(FIND_NEXT_CONTROL_JS, [dry_run, override])
    if isinstance(result, str):
        result = json.loads(result)
    if not isinstance(result, dict):
        return {"found": False, "signal": None, "text": None}
    return result


async def _wait_for_change(
    driver: PageDriver,
    base_path: str,
    origin: str,
    min_depth: int,
    baseline: set[str],
    suppress_selector: str | None = None,
) -> bool:
    """Poll the matcher until the collected set differs from ``baseline``.

    Returns ``True`` on observed change within :data:`SETTLE_TIMEOUT_SEC`,
    ``False`` on timeout. Matcher exceptions during navigation are
    swallowed — they indicate an in-flight page load and the poll
    should retry, not fail the walker.

    ``suppress_selector`` must match the value used for the surrounding
    collects: the settle poll compares its probe against ``baseline``,
    so a probe run under different matcher arguments would compare two
    differently-filtered sets and could report a spurious change.
    """
    deadline = time.monotonic() + SETTLE_TIMEOUT_SEC
    while time.monotonic() < deadline:
        await asyncio.sleep(POLL_INTERVAL_SEC)
        try:
            probe = await _run_matcher(
                driver, base_path, origin, min_depth, suppress_selector
            )
        except Exception:
            # Page is likely mid-navigation; try again on the next tick.
            continue
        if set(probe) != baseline:
            return True
    return False


async def walk_and_collect(
    driver: PageDriver,
    base_path: str,
    origin: str,
    min_depth: int,
    *,
    max_pages: int = MAX_PAGES,
    on_state: Callable[[int], Awaitable[None]] | None = None,
    next_control_override: str | None = None,
    suppress_selector: str | None = None,
) -> set[str]:
    """Collect job-link hrefs across paginated DOM states.

    The loop:

    1. Collect state 1 with the matcher; call ``on_state(1)`` if given.
    2. Repeatedly, up to ``max_pages`` total states:
        a. Run the discovery pass (non-dry-run), passing
           ``next_control_override`` through to the JS asset. If no
           control is found, terminate.
        b. Click the stamped ``[data-jal-next]`` marker. Any
           click failure terminates.
        c. Poll the matcher every :data:`POLL_INTERVAL_SEC` until the
           collected set differs from the pre-click set or
           :data:`SETTLE_TIMEOUT_SEC` elapses. Timeout terminates.
        d. Sleep :data:`MOUNT_GRACE_SEC` to absorb any tail of the
           new state's mount animation, then do a final collect.
        e. If the new state contributed zero URLs not already in the
           union (cyclic wrap-around, or a Next that swaps in the
           same links), terminate. Otherwise call ``on_state(N)``
           and continue.

    Termination is exhaustive: no-control, click failure, settle
    timeout, zero-new-links, or the ``max_pages`` cap. Whichever fires
    first wins. Returns the unioned set of URLs across every state
    successfully collected (state 1 is always included).

    ``on_state`` is invoked once per **successfully collected** state
    with the 1-based state index. It is not called for a state whose
    click/settle failed. Errors raised from ``on_state`` propagate to
    the caller — the callback is trusted (used by the capture script
    to bake per-state HTML).

    Args:
        driver: A :class:`PageDriver` implementation over the live page.
        base_path: Matcher ``basePath`` (see the JS asset for shape rules).
        origin: Matcher ``careerOrigin``.
        min_depth: Matcher ``minDepth`` floor for the id-in-path branch.
        max_pages: Upper bound on states collected in this invocation.
            Defaults to :data:`MAX_PAGES`.
        on_state: Optional async callback invoked with the 1-based
            state index after each successful state collect.
        next_control_override: Optional CSS selector passed to every
            discovery pass. When set, the JS asset's six-signal
            cascade is skipped and the first ``document.querySelector``
            match on each state is stamped as the Next control (SYS-12
            ``RuntimeHooks.next_control_selector``). The override is
            re-evaluated per state — this is deliberate: SPAs may
            mount a fresh Next affordance on every advance, so the
            same-selector-per-state semantics match how a human would
            click through.
        suppress_selector: Optional CSS selector (SYS-14, mirrors
            ``Company.link_rule.suppress_ancestor_selector``) applied to
            every matcher run in the walk — state 1, the settle probes,
            and each per-state collect. Uniformity matters: the settle
            poll diffs its probe against the previous state's set, so
            running the probe under different matcher arguments would
            compare differently-filtered sets.
    """
    urls: set[str] = set()

    # State 1: always collected before any click / discovery.
    initial = await _run_matcher(
        driver, base_path, origin, min_depth, suppress_selector
    )
    urls.update(initial)
    if on_state is not None:
        await on_state(1)

    baseline: set[str] = set(initial)

    for state_index in range(2, max_pages + 1):
        descriptor = await _find_next(
            driver, dry_run=False, override=next_control_override
        )
        if not descriptor.get("found"):
            break

        try:
            await driver.click(_NEXT_MARKER_SELECTOR)
        except Exception:
            # Click failed to fire — treat identically to a missing
            # control. Do not raise: the walker is best-effort and
            # returns whatever it has collected so far.
            break

        settled = await _wait_for_change(
            driver, base_path, origin, min_depth, baseline, suppress_selector
        )
        if not settled:
            break

        await asyncio.sleep(MOUNT_GRACE_SEC)
        current = await _run_matcher(
            driver, base_path, origin, min_depth, suppress_selector
        )
        current_set = set(current)
        new_urls = current_set - urls
        if not new_urls:
            # Cyclic pagination (last-page Next wrapping to page 1) or
            # a Next that swapped in the same anchors — no forward
            # progress possible, stop.
            break
        urls |= new_urls
        baseline = current_set

        if on_state is not None:
            await on_state(state_index)

    return urls


# ---------------------------------------------------------------------------
# Hook execution (SYS-12)
# ---------------------------------------------------------------------------


async def apply_pre_extract_css(driver: PageDriver, css: str) -> int:
    """Inject ``css`` as a ``<style data-jal-css>`` in the top document.

    Idempotent: re-invoking with the same or different payload replaces
    the prior element rather than accumulating. Returns the parsed
    ``CSSStyleSheet.cssRules.length`` so callers can log the visible
    rule count; a non-whitespace payload that parses to zero rules
    raises loudly in the JS layer (surfaced here as the driver's
    ``evaluate`` exception) rather than silently succeeding.

    Whitespace-only payloads (``""``, ``"   "``) return ``0`` without
    raising — the ``RuntimeHooks`` schema validators reject those at
    catalog-import time, so reaching this function with such a payload
    is a programmer error, not a user-facing case, but we tolerate it
    to keep the JS body's edge cases explicit.

    Args:
        driver: :class:`PageDriver` implementation over the live page.
        css: The CSS payload to inject. Must be non-empty after strip;
            payload lifecycle is one document — SPA-persistent state 2+
            keeps the injection, hard-nav pagination discards it.

    Returns:
        Number of top-level rules the browser parsed from the payload.
    """
    result = await driver.evaluate(_INJECT_CSS_JS, css)
    # Settle before returning, so the caller's next step — the matcher,
    # ``expand_all``, or the capture bake — observes the *applied* style.
    # If the overridden property is under a CSS transition, the new value
    # is not observable until the browser's next rendering update starts
    # that transition, and a forced reflow in the same tick does not help
    # (see ``CSS_SETTLE_SEC``). Skipping this is a total loss, not a
    # partial one: on Accenture, whose job anchors sit in collapsed
    # ``visibility: hidden`` accordion wrappers, the first matcher run saw
    # 0 of 12 anchors and the paginated union came back 44 of 56 — states
    # 2+ were rescued only by the incidental settle after each next-page
    # click. With the settle: 12 and 56. Lives inside this function
    # rather than at the call site so the runtime and
    # ``scripts/capture_snapshot.py`` stay in lockstep, the same parity
    # rule the walker's settle constants follow.
    await asyncio.sleep(CSS_SETTLE_SEC)
    if isinstance(result, str):
        try:
            return int(result)
        except (ValueError, TypeError):
            return 0
    if isinstance(result, int | float):
        return int(result)
    return 0


async def expand_all(
    driver: PageDriver,
    selector: str,
    *,
    max_rounds: int = EXPAND_MAX_ROUNDS,
) -> int:
    """Click every visible match of ``selector`` in bounded rounds.

    One round is:

    1. Stamp every CSS-visible match of ``selector`` with an indexed
       ``data-jal-expand="<k>"`` attribute. Stale markers from a
       previous round are cleared before stamping.
    2. If the stamp count is zero, terminate — the selector matches
       nothing visible on the current DOM state.
    3. For each stamped index ``k`` in ``[0, count)``, click
       ``[data-jal-expand="k"]`` through the :class:`PageDriver` seam.
       Per-click failures are swallowed (a click that navigates the
       page out from under the walker, or an element that has become
       detached between stamp and click, must not fail the entire
       expansion).
    4. Clear every ``data-jal-expand`` marker (defensive; the next
       round's stamp also clears).
    5. Sleep :data:`EXPAND_SETTLE_SEC` seconds to absorb the tail of
       the SPA's mount / animation before the next stamp query. The
       constant is read from the module at call time so
       ``monkeypatch.setattr`` in tests shrinks the wait for the
       current invocation.

    The loop terminates on a zero-stamp round or after ``max_rounds``
    rounds have fired, whichever comes first. ``max_rounds`` is a
    default keyword argument bound at function-definition time and is
    therefore not affected by monkeypatching :data:`EXPAND_MAX_ROUNDS`
    — tests that need a tighter cap must pass ``max_rounds=`` at the
    call site.

    Args:
        driver: :class:`PageDriver` implementation over the live page.
        selector: CSS selector for expand triggers (e.g. accordion
            headers or "Show more" buttons). Passing a whitespace-only
            or ``None``-shaped selector is a programmer error: the
            ``RuntimeHooks`` validators reject them at catalog-import
            time.
        max_rounds: Maximum stamp/click rounds. Defaults to
            :data:`EXPAND_MAX_ROUNDS`.

    Returns:
        Total number of clicks attempted across every round (successes
        and swallowed failures alike). Callable primarily for diagnostic
        logging — the caller does not gate downstream behaviour on it.
    """
    total_clicks = 0
    for _round_index in range(max_rounds):
        raw_count = await driver.evaluate(
            _STAMP_EXPAND_TARGETS_JS, [selector, _EXPAND_MARKER]
        )
        # Adapters may return int, float, or (via browser-use) a JSON-
        # decoded str. Normalise defensively.
        if isinstance(raw_count, str):
            try:
                count = int(raw_count)
            except (ValueError, TypeError):
                count = 0
        elif isinstance(raw_count, int | float):
            count = int(raw_count)
        else:
            count = 0

        if count <= 0:
            break

        for k in range(count):
            # Per-click failures are swallowed by design (see docstring):
            # a click that navigates the page out from under the walker
            # or hits an element detached between stamp and click must
            # not fail the entire expansion. The round's remaining
            # clicks still fire and ``total_clicks`` still increments.
            with suppress(Exception):
                await driver.click(f'[{_EXPAND_MARKER}="{k}"]')
            total_clicks += 1

        await driver.evaluate(_CLEAR_EXPAND_MARKERS_JS, _EXPAND_MARKER)
        # Module-level lookup so a test's monkeypatch is honoured on
        # each iteration (mirrors ``_wait_for_change``'s treatment of
        # ``SETTLE_TIMEOUT_SEC``).
        await asyncio.sleep(EXPAND_SETTLE_SEC)

    return total_clicks


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def collect_job_links(
    browser_session: BrowserSession,
    base_path: str,
    origin: str,
    *,
    min_depth: int = 1,
    paginate: bool = False,
    hooks: RuntimeHooks | None = None,
    suppress_selector: str | None = None,
) -> list[str]:
    """Run the DOM matcher against the session's current page.

    Returns the list of same-origin job-link hrefs the matcher found, or
    an empty list when the page has no active tab.

    Args:
        browser_session: Live browser-use session.
        base_path: Same-origin path prefix that valid job links must
            start with (or equal, for the id-in-query shape).
        origin: ``"{scheme}://{netloc}"`` of the job board — job links
            must share this origin.
        min_depth: Minimum path-tail depth in the id-in-path branch of
            the matcher (mirrors ``LinkRule.min_depth``). Floor semantics
            — the default of ``1`` keeps every anchor whose path starts
            with ``<base_path>/``; raise it when the board's chrome
            links share the prefix at shallow depths and real postings
            live deeper (C9: Databricks, Avionyx/iCIMS).
        paginate: When ``False`` (default) run a single matcher pass
            over the current DOM state — byte-identical to the pre-SYS-5
            behaviour when ``hooks`` is inert. When ``True`` delegate to
            :func:`walk_and_collect`, which walks paginated states via
            the ``FIND_NEXT_CONTROL_JS`` discovery pass and a
            driver-side click. Paginated results are returned sorted
            for run-to-run determinism.
        hooks: Optional :class:`RuntimeHooks` executed between agent
            handoff and matcher invocation (SYS-12). ``None`` and an
            inert :class:`RuntimeHooks` are equivalent — both keep the
            single-shot ``paginate=False`` path byte-identical to the
            pre-SYS-12 shape. When non-inert (or under ``paginate=True``
            regardless of hooks) an :class:`ActorPageDriver` is built
            once and the hooks fire in the order documented at module
            scope: pre-extract CSS injection, then bounded expand
            rounds, then the matcher (or the walker) runs against the
            prepared DOM. ``filter_already_applied`` is read by the
            prompt-rendering layer only and is not consulted here.
        suppress_selector: Optional CSS selector (SYS-14, mirrors
            ``Company.link_rule.suppress_ancestor_selector``). Anchors
            whose ``closest(selector)`` is non-null are dropped by the
            matcher, after its visibility gate and before URL
            bucketing. ``None`` (the default) disables the gate and is
            byte-identical to the pre-SYS-14 matcher. Unlike ``hooks``,
            this is *not* page preparation — it never touches the DOM,
            it only narrows what the matcher counts, so it applies
            uniformly on the inert fast path, the single-shot driver
            path, and every state of the walker.
    """
    page = await browser_session.get_current_page()
    if page is None:
        return []

    # Inert path: no hooks, no pagination → the pre-SYS-12 single-shot
    # shape. Construct no driver, no adapter overhead — the runtime
    # cost of the 51 pre-SYS-12 corpus companies must not regress.
    # ``suppress_selector`` rides along as the matcher's fourth
    # argument here rather than forcing the driver path: it is a
    # matcher argument, not a page mutation, so it costs nothing.
    if (hooks is None or hooks.is_inert) and not paginate:
        urls = await page.evaluate(
            EXTRACT_JOB_LINKS_JS, [base_path, origin, min_depth, suppress_selector]
        )
        # page.evaluate can return the JS return value already parsed, or as
        # a JSON string when the driver serialises it — handle both.
        if isinstance(urls, str):
            urls = json.loads(urls)
        return urls or []

    driver = ActorPageDriver(page)

    # SYS-12 hook phase — fires in the pinned order (CSS then expand)
    # before either the single-shot matcher or the walker sees the DOM.
    # Skipped entirely when hooks are inert (``paginate=True`` alone).
    if hooks is not None and not hooks.is_inert:
        if hooks.pre_extract_css is not None:
            await apply_pre_extract_css(driver, hooks.pre_extract_css)
        if hooks.expand_selector is not None:
            await expand_all(driver, hooks.expand_selector)

    if not paginate:
        return await _run_matcher(
            driver, base_path, origin, min_depth, suppress_selector
        )

    # When a non-inert hook pins ``next_control_selector`` it is
    # forwarded to every discovery pass inside the walker; otherwise
    # (paginate=True with no override, or inert hooks) the walker's
    # standard six-signal cascade runs.
    override = (
        hooks.next_control_selector
        if hooks is not None and not hooks.is_inert
        else None
    )
    collected = await walk_and_collect(
        driver,
        base_path,
        origin,
        min_depth,
        next_control_override=override,
        suppress_selector=suppress_selector,
    )
    return sorted(collected)
