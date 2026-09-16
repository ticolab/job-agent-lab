"""Browser-dependent tests for ``scripts/probe_board.py``.

The unit-tested pure functions in ``tests/unit/test_probe_logic.py`` cover
the fingerprint table, the min_depth heuristic, and the suggestion
assembly. This file exercises the JS payloads (``_CENSUS_JS``,
``_FILTER_CENSUS_JS``, ``_DOM_MARKERS_JS``) against synthetic pages
built with ``page.set_content``, so the browser is the system under
test but the pages are pinned in-file rather than depending on a live
board.

The parent ``tests/snapshots/conftest.py`` disables page JavaScript to
keep captured HTML deterministic. The probe census depends on live JS
evaluation for visibility, shadow-root traversal, and select-option
enumeration, so this file overrides the ``browser_context_args`` fixture
at module scope to re-enable JS. The override is local to this file —
the surrounding snapshot suite is not affected.

Loading pattern matches the unit test: register the probe under
``sys.modules['probe_board']`` before executing the loader so its
dataclasses can resolve their own module.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from playwright.sync_api import Page

_PROBE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "probe_board.py"


@pytest.fixture(scope="module")
def probe() -> ModuleType:
    """Load the probe script as an importable module."""
    spec = importlib.util.spec_from_file_location("probe_board", _PROBE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["probe_board"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def browser_context_args(
    browser_context_args: dict[str, Any],
) -> dict[str, Any]:
    """Re-enable JS in this module (parent conftest disables it).

    The probe census evaluates JS in-page — visibility, shadow roots,
    select-option enumeration — so a JS-disabled context would produce
    all-zero results and hide real bugs.
    """
    return {**browser_context_args, "java_script_enabled": True}


# ---------------------------------------------------------------------------
# _CENSUS_JS
# ---------------------------------------------------------------------------


class TestCensusJs:
    """The probe-owned anchor census used across sections 1 & 2 of the report."""

    def test_visible_and_hidden_anchors_split(
        self, page: Page, probe: ModuleType
    ) -> None:
        # Two prefix-matching anchors; one hidden via ``display: none``,
        # one visible. Matcher-visible must be 1, hidden_count 1,
        # eligible_incl_hidden 2.
        page.set_content(
            """
            <html><body>
              <a href="https://example.com/jobs/1-visible">visible</a>
              <a href="https://example.com/jobs/2-hidden"
                 style="display: none">hidden</a>
              <a href="https://other.com/jobs/3">off-origin</a>
              <a href="https://example.com/about">non-matching path</a>
            </body></html>
            """,
            wait_until="load",
        )
        result = page.evaluate(probe._CENSUS_JS, ["/jobs", "https://example.com", 1])
        assert result["eligible_incl_hidden"] == 2
        assert result["matcher_visible"] == 1
        assert result["hidden_count"] == 1
        # No shadow roots present.
        assert result["open_shadow_root_count"] == 0
        assert result["piercing_recovery"] == 0

    def test_shadow_dom_piercing_delta(self, page: Page, probe: ModuleType) -> None:
        # A prefix-matching anchor lives inside an open shadow root; the
        # light-DOM pass misses it, the piercing pass recovers it.
        page.set_content(
            """
            <html><body>
              <div id="host"></div>
              <script>
                const host = document.getElementById('host');
                const root = host.attachShadow({ mode: 'open' });
                const a = document.createElement('a');
                a.href = 'https://example.com/jobs/1-shadow';
                a.textContent = 'shadow anchor';
                root.appendChild(a);
              </script>
            </body></html>
            """,
            wait_until="load",
        )
        result = page.evaluate(probe._CENSUS_JS, ["/jobs", "https://example.com", 1])
        assert result["open_shadow_root_count"] == 1
        assert result["light_dom_matcher_visible"] == 0
        assert result["matcher_visible"] == 1
        assert result["piercing_recovery"] == 1

    def test_depth_histogram_populated(self, page: Page, probe: ModuleType) -> None:
        # Three anchors under ``/jobs`` at depths 1, 2, and 2. The
        # histogram must count them accurately so the C9 heuristic in
        # ``suggest_min_depth`` has ground truth.
        page.set_content(
            """
            <html><body>
              <a href="https://example.com/jobs/one">d1</a>
              <a href="https://example.com/jobs/eng/two">d2a</a>
              <a href="https://example.com/jobs/eng/three">d2b</a>
            </body></html>
            """,
            wait_until="load",
        )
        result = page.evaluate(probe._CENSUS_JS, ["/jobs", "https://example.com", 1])
        # JS returns string keys.
        assert result["depth_histogram"] == {"1": 1, "2": 2}
        assert result["matcher_visible"] == 3

    def test_fragment_variants_collapsed(self, page: Page, probe: ModuleType) -> None:
        # ``/jobs/1`` and ``/jobs/1#apply`` are the same canonical URL —
        # the census must count the second one as a fragment variant
        # rather than a distinct match.
        page.set_content(
            """
            <html><body>
              <a href="https://example.com/jobs/1">canonical</a>
              <a href="https://example.com/jobs/1#apply">with fragment</a>
            </body></html>
            """,
            wait_until="load",
        )
        result = page.evaluate(probe._CENSUS_JS, ["/jobs", "https://example.com", 1])
        assert result["fragment_self_links"] == 1
        assert result["fragment_variants"] == 1
        # Both anchors are eligible; both are visible.
        assert result["eligible_incl_hidden"] == 2
        assert result["matcher_visible"] == 2


# ---------------------------------------------------------------------------
# _FILTER_CENSUS_JS
# ---------------------------------------------------------------------------


class TestFilterCensusJs:
    """The filter census used for section 5 (C2 / C3 signals)."""

    def test_native_select_with_location_tokens(
        self, page: Page, probe: ModuleType
    ) -> None:
        # A ``<select name="country">`` with an option for Costa Rica —
        # exactly the shape the C3 clause teaches the agent to
        # drive. The probe reports it under section 5.
        page.set_content(
            """
            <html><body>
              <label for="loc">Location</label>
              <select id="loc" name="country">
                <option value="">All</option>
                <option value="CR">Costa Rica</option>
                <option value="US">United States</option>
              </select>
              <select name="unrelated">
                <option>A</option>
              </select>
            </body></html>
            """,
            wait_until="load",
        )
        result = page.evaluate(
            probe._FILTER_CENSUS_JS,
            [list(probe.LOCATION_TOKENS), probe.SELECT_SAMPLE_OPTION_CAP],
        )
        # Only the location-token-matching select is reported.
        assert len(result["selects"]) == 1
        entry = result["selects"][0]
        assert entry["name"] == "country"
        assert entry["id"] == "loc"
        assert "Costa Rica" in entry["sample_options"]

    def test_aria_combobox_reported(self, page: Page, probe: ModuleType) -> None:
        page.set_content(
            """
            <html><body>
              <button role="combobox" aria-label="Location filter">
                Costa Rica
              </button>
            </body></html>
            """,
            wait_until="load",
        )
        result = page.evaluate(
            probe._FILTER_CENSUS_JS,
            [list(probe.LOCATION_TOKENS), probe.SELECT_SAMPLE_OPTION_CAP],
        )
        assert len(result["comboboxes"]) == 1
        assert result["comboboxes"][0]["tag"] == "button"

    def test_bare_search_box_reported(self, page: Page, probe: ModuleType) -> None:
        # A visible text input with a non-location-shaped placeholder
        # surfaces as a C2-signal bare search box.
        page.set_content(
            """
            <html><body>
              <input type="text" placeholder="Search jobs by keyword">
            </body></html>
            """,
            wait_until="load",
        )
        result = page.evaluate(
            probe._FILTER_CENSUS_JS,
            [list(probe.LOCATION_TOKENS), probe.SELECT_SAMPLE_OPTION_CAP],
        )
        assert len(result["search_boxes"]) == 1
        assert "keyword" in (result["search_boxes"][0]["label"] or "").lower()

    def test_location_shaped_input_not_counted_as_bare_search(
        self, page: Page, probe: ModuleType
    ) -> None:
        # An input whose placeholder / name matches a location token
        # belongs in another bucket (the agent would drive it as a
        # filter); reporting it under search_boxes muddles C2 vs C3.
        page.set_content(
            """
            <html><body>
              <input type="text" name="city" placeholder="City">
            </body></html>
            """,
            wait_until="load",
        )
        result = page.evaluate(
            probe._FILTER_CENSUS_JS,
            [list(probe.LOCATION_TOKENS), probe.SELECT_SAMPLE_OPTION_CAP],
        )
        assert result["search_boxes"] == []


# ---------------------------------------------------------------------------
# _DOM_MARKERS_JS
# ---------------------------------------------------------------------------


class TestDomMarkersJs:
    """The DOM-marker sweep used by the fingerprint evaluator."""

    def test_matching_selector_returned(self, page: Page, probe: ModuleType) -> None:
        page.set_content(
            """
            <html><body>
              <div class="grnhse_iframe"></div>
            </body></html>
            """,
            wait_until="load",
        )
        hits = page.evaluate(
            probe._DOM_MARKERS_JS,
            [".grnhse_iframe", "[data-gh_jid]"],
        )
        assert hits == [".grnhse_iframe"]

    def test_malformed_selector_is_swallowed(
        self, page: Page, probe: ModuleType
    ) -> None:
        # A malformed selector must not blow up the whole probe — it
        # should simply produce no hit and let the other selectors
        # continue to evaluate.
        page.set_content("<html><body><div id='x'></div></body></html>")
        hits = page.evaluate(
            probe._DOM_MARKERS_JS,
            ["not a valid ::: selector", "#x"],
        )
        assert hits == ["#x"]
