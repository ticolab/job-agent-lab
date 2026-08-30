"""In-Chromium tests for the SYS-12 hook-execution helpers.

This module pins the helper *primitives in isolation*. Two siblings
own the rest of the SYS-12 surface: ``test_runtime_hooks.py`` is the
canonical file for the *joint* mechanics (two or more helpers plus the
matcher running against the same DOM, including §4.5 execution order
and the combined css + override walk across paginated states), and
``test_pagination_walker.py`` owns ``walk_and_collect``'s
``next_control_override`` configurations. Add a new case to whichever
file matches that split.

The two helpers under test are :func:`apply_pre_extract_css` and
:func:`expand_all` in
:mod:`job_agent_lab.extraction.dom.collector`. Both run under an
:class:`ActorPageDriver` at runtime; here they are driven against a
:class:`_HookTestDriver` wrapping a sync-Playwright ``Page`` on tiny
``file://`` pages so the assertions are against real Chromium
behaviour — the ``CSSStyleSheet`` parser for the CSS helper, the
matcher-shared ``checkVisibility({checkVisibilityCSS: true})`` gate
for the expander.

The coroutine-drive pattern mirrors ``test_pagination_walker`` (see
that module's docstring for the rationale — pytest-playwright's sync
API leaves an ambient greenlet loop that makes ``asyncio.run`` refuse
to start a new loop, and the worker-thread pattern breaks against
sync Playwright's thread-affine handles). All ``await`` points in the
helpers resolve synchronously under this driver: the driver methods
forward to sync-Playwright calls without yielding, and the
``fast_hooks`` fixture below replaces ``collector_mod.asyncio.sleep``
with a synchronous fake so the inter-round sleep never yields either.

Click-swallow behaviour (per-click ``Exception`` inside a stamped
round must not fail the entire expansion) is covered by an in-file
:class:`_MockDriver` rather than a Chromium page. Producing a
deterministic mid-round click exception in sync-Playwright requires
either a millisecond-scale click timeout (fragile against CI jitter)
or DOM-mutation racing (unreliable in a JS-disabled ``file://``
context). The mock driver scripts the exception directly on a
specific stamp index while still exercising the real ``expand_all``
loop.
"""

from __future__ import annotations

import time
from collections.abc import Coroutine, Iterable
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, BrowserContext, Page

from job_agent_lab.extraction.dom import collector as collector_mod
from job_agent_lab.extraction.dom.collector import (
    _EXPAND_MARKER,
    apply_pre_extract_css,
    expand_all,
)

# ---------------------------------------------------------------------------
# Manual coroutine driver
# ---------------------------------------------------------------------------


def _drive(coro: Coroutine[Any, Any, Any]) -> Any:
    """Drive ``coro`` to completion without a real event loop."""
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
# Drivers
# ---------------------------------------------------------------------------


class _HookTestDriver:
    """Async ``PageDriver`` shim over a sync Playwright ``Page``.

    Mirrors ``test_pagination_walker._TestDriver`` — the async methods
    forward directly to blocking sync-Playwright calls with no
    ``await`` inside, so ``_drive`` completes them via a single
    ``StopIteration``.
    """

    def __init__(self, page: Page) -> None:
        self._page = page

    async def evaluate(self, js: str, arg: Any) -> Any:
        return self._page.evaluate(js, arg)

    async def click(self, selector: str) -> None:
        # Short timeout so a missing selector fails fast rather than
        # blocking the test on Playwright's 30s default auto-wait.
        self._page.click(selector, timeout=1000)

    async def url(self) -> str:
        result: str = self._page.url
        return result


class _MockDriver:
    """In-process ``PageDriver`` used for the click-swallow test.

    ``evaluate`` returns caller-scripted values by JS-body substring
    dispatch (a lightweight router that matches on identifying strings
    in the ``expand_all`` helper's JS payloads). ``click`` raises for
    selectors whose stamp index is in ``fail_indices`` and succeeds
    otherwise; every attempted click is recorded on ``clicks``.
    """

    def __init__(
        self,
        *,
        stamp_counts: list[int],
        fail_indices: set[int] | None = None,
    ) -> None:
        # Sequential stamp results per round. Consumed in order — the
        # test scripts ``expand_all``'s termination by supplying a
        # trailing ``0`` (zero-stamp round terminates).
        self._stamp_counts = list(stamp_counts)
        self._fail_indices = fail_indices or set()
        self.clicks: list[str] = []
        # Rule-count return for CSS injection is not exercised here.

    async def evaluate(self, js: str, arg: Any) -> Any:
        if "querySelectorAll(selector)" in js:
            # _STAMP_EXPAND_TARGETS_JS
            if not self._stamp_counts:
                return 0
            return self._stamp_counts.pop(0)
        if "querySelectorAll('[' + marker + ']')" in js:
            # _CLEAR_EXPAND_MARKERS_JS — return value is unused by the
            # helper, but a truthful shape keeps the mock honest.
            return 0
        raise AssertionError(f"unexpected evaluate js payload: {js[:80]!r}")

    async def click(self, selector: str) -> None:
        self.clicks.append(selector)
        # Extract the stamp index from ``[data-jal-expand="<k>"]``.
        marker = _EXPAND_MARKER
        prefix = f'[{marker}="'
        if selector.startswith(prefix) and selector.endswith('"]'):
            idx_str = selector[len(prefix) : -2]
            if idx_str.isdigit() and int(idx_str) in self._fail_indices:
                raise RuntimeError(f"scripted click failure at index {idx_str}")

    async def url(self) -> str:
        return "mock://test"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def js_context(browser: Browser) -> Iterable[BrowserContext]:
    """A JS-enabled context; the snapshot conftest's JS-off is bypassed.

    ``tests/snapshots/conftest.py`` disables page JS at the auto-created
    context level so snapshot ``<script>`` tags cannot re-hydrate DOM.
    The hook helpers rely on real click handlers (accordion expand) and
    the real CSS parser, so this fixture constructs an explicit
    JS-enabled context that bypasses the override.
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
    """Shrink ``EXPAND_SETTLE_SEC`` and make ``asyncio.sleep`` synchronous.

    The production ``EXPAND_SETTLE_SEC`` (0.7s) exists to absorb SPA
    mount/animation tails; tests use ``file://`` pages that mount
    synchronously, so shrinking the value avoids real wallclock waits.
    Replacing ``asyncio.sleep`` with a synchronous fake keeps ``_drive``
    from encountering a scheduler yield. ``EXPAND_MAX_ROUNDS`` is left
    alone — the cap test passes ``max_rounds=`` explicitly.
    """
    monkeypatch.setattr(collector_mod, "EXPAND_SETTLE_SEC", 0.01)

    async def _sync_sleep(seconds: float) -> None:
        time.sleep(seconds)

    monkeypatch.setattr(collector_mod.asyncio, "sleep", _sync_sleep)


# ---------------------------------------------------------------------------
# apply_pre_extract_css
# ---------------------------------------------------------------------------


def _empty_page_uri(tmp_path: Path) -> str:
    """Write a minimal HTML doc and return its ``file://`` URI."""
    p = tmp_path / "index.html"
    p.write_text(
        "<!DOCTYPE html><html><head></head><body></body></html>",
        encoding="utf-8",
    )
    return p.as_uri()


class TestApplyPreExtractCss:
    """CSS injection semantics: rule-count, idempotency, invalid raise."""

    def test_valid_css_returns_rule_count(
        self, tmp_path: Path, js_page: Page, fast_hooks: None
    ) -> None:
        """A payload with N parseable rules returns N."""
        js_page.goto(_empty_page_uri(tmp_path))
        driver = _HookTestDriver(js_page)

        css = "a.hidden { display: block !important; } .foo { color: red; }"
        rules = _drive(apply_pre_extract_css(driver, css))

        assert rules == 2
        # <style data-jal-css> is present in <head>.
        style_count = js_page.evaluate(
            "() => document.head.querySelectorAll('style[data-jal-css]').length"
        )
        assert style_count == 1

    def test_re_run_replaces_previous_style(
        self, tmp_path: Path, js_page: Page, fast_hooks: None
    ) -> None:
        """Running twice must leave exactly one ``<style data-jal-css>``."""
        js_page.goto(_empty_page_uri(tmp_path))
        driver = _HookTestDriver(js_page)

        _drive(apply_pre_extract_css(driver, ".a { color: red; }"))
        second_rules = _drive(
            apply_pre_extract_css(driver, ".b { color: blue; } .c { color: green; }")
        )

        assert second_rules == 2
        style_count = js_page.evaluate(
            "() => document.head.querySelectorAll('style[data-jal-css]').length"
        )
        assert style_count == 1
        # The second payload is the one that survived.
        current_text = js_page.evaluate(
            "() => document.head.querySelector('style[data-jal-css]').textContent"
        )
        assert ".b" in current_text and ".a" not in current_text

    def test_invalid_css_raises(
        self, tmp_path: Path, js_page: Page, fast_hooks: None
    ) -> None:
        """A non-whitespace payload that parses to zero rules must raise."""
        js_page.goto(_empty_page_uri(tmp_path))
        driver = _HookTestDriver(js_page)

        # Chromium's CSS parser silently drops malformed rules; ``!!!``
        # alone yields zero rules despite being non-whitespace input.
        with pytest.raises(Exception, match="zero CSS rules"):
            _drive(apply_pre_extract_css(driver, "!!! not css !!!"))

    def test_whitespace_only_returns_zero_without_raising(
        self, tmp_path: Path, js_page: Page, fast_hooks: None
    ) -> None:
        """Whitespace payloads are inert: return 0, no raise.

        The ``RuntimeHooks`` validators reject whitespace-only payloads
        at catalog-import time, so this case is not reachable from a
        real ``Company`` entry. The JS-side edge case is still explicit
        so a future caller passing a whitespace payload directly does
        not get a confusing raise from the CSS parser.
        """
        js_page.goto(_empty_page_uri(tmp_path))
        driver = _HookTestDriver(js_page)

        rules = _drive(apply_pre_extract_css(driver, "   \n  "))
        assert rules == 0


# ---------------------------------------------------------------------------
# expand_all — Chromium
# ---------------------------------------------------------------------------


_EXPAND_PAGE_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
  <style>
    .child {{ display: none; }}
    .child.open {{ display: block; }}
    .hidden-trigger {{ display: none; }}
  </style>
</head>
<body>
{body}
<script>
// Real accordions stop matching the ".trigger" selector once expanded
// (the button's ARIA state flips, a different affordance replaces the
// closed one, etc.). The fixture models that shape by stripping the
// "trigger" class on click so the next stamp round finds a smaller
// candidate set — otherwise the expander would re-click the same
// headers up to EXPAND_MAX_ROUNDS times.
document.querySelectorAll('.trigger').forEach((btn, i) => {{
  btn.addEventListener('click', () => {{
    const target = document.querySelector('#child-' + i);
    if (target) target.classList.add('open');
    btn.classList.remove('trigger');
  }});
}});
</script>
</body>
</html>"""


def _write_expand_page(
    tmp_path: Path,
    *,
    visible_triggers: int,
    hidden_triggers: int = 0,
) -> str:
    """Write an expand-fixture page with N visible + M hidden triggers.

    Each visible trigger is a ``<button class="trigger">`` whose click
    handler reveals a sibling ``.child`` div (initially ``display: none``
    via the stylesheet). Hidden triggers additionally carry
    ``hidden-trigger`` which forces ``display: none`` on the trigger
    itself — the expander's visibility gate must skip these.
    """
    parts: list[str] = []
    trigger_idx = 0
    for _ in range(visible_triggers):
        parts.append(
            f'<button class="trigger" data-idx="{trigger_idx}">'
            f"Show {trigger_idx}</button>"
            f'<div class="child" id="child-{trigger_idx}">payload-{trigger_idx}</div>'
        )
        trigger_idx += 1
    for _ in range(hidden_triggers):
        parts.append(
            f'<button class="trigger hidden-trigger" data-idx="{trigger_idx}">'
            f"Hidden {trigger_idx}</button>"
            f'<div class="child" id="child-{trigger_idx}">payload-{trigger_idx}</div>'
        )
        trigger_idx += 1

    html = _EXPAND_PAGE_TEMPLATE.format(body="\n".join(parts))
    p = tmp_path / "expand.html"
    p.write_text(html, encoding="utf-8")
    return p.as_uri()


class TestExpandAll:
    """Expansion semantics: happy path, visibility gate, zero-match, cap."""

    def test_happy_path_reveals_children(
        self, tmp_path: Path, js_page: Page, fast_hooks: None
    ) -> None:
        """Every visible trigger fires and its child becomes visible."""
        uri = _write_expand_page(tmp_path, visible_triggers=3)
        js_page.goto(uri)
        driver = _HookTestDriver(js_page)

        total_clicks = _drive(expand_all(driver, ".trigger"))

        assert total_clicks == 3
        # All three children are now visible.
        visible_children = js_page.evaluate(
            "() => document.querySelectorAll('.child.open').length"
        )
        assert visible_children == 3

    def test_hidden_matches_skipped(
        self, tmp_path: Path, js_page: Page, fast_hooks: None
    ) -> None:
        """CSS-hidden triggers must not be stamped or clicked.

        Two visible triggers plus two hidden ones (``display: none``) —
        the expander's ``checkVisibility({checkVisibilityCSS: true})``
        gate must skip both hidden triggers, so ``expand_all`` reports
        exactly 2 clicks and only the visible triggers' children open.
        """
        uri = _write_expand_page(tmp_path, visible_triggers=2, hidden_triggers=2)
        js_page.goto(uri)
        driver = _HookTestDriver(js_page)

        total_clicks = _drive(expand_all(driver, ".trigger"))

        assert total_clicks == 2
        visible_children = js_page.evaluate(
            "() => document.querySelectorAll('.child.open').length"
        )
        assert visible_children == 2

    def test_zero_match_terminates_cleanly(
        self, tmp_path: Path, js_page: Page, fast_hooks: None
    ) -> None:
        """A selector that matches nothing returns 0 with no clicks."""
        uri = _write_expand_page(tmp_path, visible_triggers=0)
        js_page.goto(uri)
        driver = _HookTestDriver(js_page)

        total_clicks = _drive(expand_all(driver, ".no-such-thing"))
        assert total_clicks == 0

    def test_max_rounds_cap_terminates_expansion(
        self, tmp_path: Path, js_page: Page, fast_hooks: None
    ) -> None:
        """The ``max_rounds`` cap terminates before natural exhaustion.

        The fixture below reveals ONE new trigger per round in an
        unbounded chain. With ``max_rounds=2`` the expander must fire
        exactly 2 rounds (2 clicks total) and stop, even though a third
        round's stamp would find another visible trigger.
        """
        chain_page = tmp_path / "chain.html"
        chain_page.write_text(
            "<!DOCTYPE html><html><body>"
            '<button class="trig" data-idx="0">Trigger</button>'
            "<script>"
            "let counter = 1;"
            "document.body.addEventListener('click', (e) => {"
            "  if (!e.target.classList.contains('trig')) return;"
            "  e.target.classList.remove('trig');"
            "  const next = document.createElement('button');"
            "  next.className = 'trig';"
            "  next.dataset.idx = counter++;"
            "  next.textContent = 'Trigger ' + next.dataset.idx;"
            "  document.body.appendChild(next);"
            "});"
            "</script>"
            "</body></html>",
            encoding="utf-8",
        )
        js_page.goto(chain_page.as_uri())
        driver = _HookTestDriver(js_page)

        total_clicks = _drive(expand_all(driver, ".trig", max_rounds=2))

        assert total_clicks == 2
        # After 2 rounds the fixture has appended 2 fresh triggers —
        # one still-visible successor confirms the loop stopped by cap
        # rather than by natural zero-stamp exhaustion.
        remaining = js_page.evaluate("() => document.querySelectorAll('.trig').length")
        assert remaining == 1


# ---------------------------------------------------------------------------
# expand_all — mock (click-swallow)
# ---------------------------------------------------------------------------


class TestExpandAllClickSwallow:
    """Per-click exceptions must not fail the round or the loop."""

    def test_per_click_exception_swallowed(self, fast_hooks: None) -> None:
        """One raised click inside a 3-stamp round must not abort.

        Round 1 stamps 3 elements. The click at index 1 raises; index 0
        and index 2 succeed. All three attempts are counted (the
        counter increments regardless of click outcome). Round 2 stamps
        0 — the loop terminates cleanly.
        """
        driver = _MockDriver(stamp_counts=[3, 0], fail_indices={1})

        total_clicks = _drive(expand_all(driver, ".irrelevant"))

        assert total_clicks == 3
        assert driver.clicks == [
            f'[{_EXPAND_MARKER}="0"]',
            f'[{_EXPAND_MARKER}="1"]',
            f'[{_EXPAND_MARKER}="2"]',
        ]
