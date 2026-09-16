"""Unit tests for the pure logic in ``scripts/probe_board.py``.

The probe script is not a package module — it lives under ``scripts/`` and
is meant to be invoked as a CLI. We load it via ``importlib`` so the
module-level constants (``FINGERPRINT_TABLE``, ``LOCATION_TOKENS``) and
the pure functions (prefix ancestry, min_depth heuristic, fingerprint
evaluation, suggestion assembly) can be exercised without a browser.

The dataclasses defined in the module require the module to be
registered in ``sys.modules`` before ``exec_module`` runs (Python's
``dataclasses.KW_ONLY`` sentinel check calls ``sys.modules.get(cls.__module__)``
during class construction). We do that in a module-level fixture so
every test picks up the same import.

Section-1 (matcher-truth headline) and section-3 (piercing delta) are
browser-dependent; those live in ``tests/snapshots/test_probe_census.py``
against synthetic ``set_content`` pages.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_PROBE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "probe_board.py"


@pytest.fixture(scope="module")
def probe() -> ModuleType:
    """Load ``scripts/probe_board.py`` as a module named ``probe_board``.

    Registering it under ``sys.modules['probe_board']`` before executing
    the loader is required so the ``@dataclass`` decorators inside the
    module can resolve their own ``__module__`` back to a live entry.
    """
    spec = importlib.util.spec_from_file_location("probe_board", _PROBE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["probe_board"] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Prefix ancestry
# ---------------------------------------------------------------------------


class TestBuildPrefixAncestry:
    """``build_prefix_ancestry`` derives the prefix + its parent chain."""

    def test_deep_prefix_expands_to_root(self, probe: ModuleType) -> None:
        # ``/company/careers/eng/1234-senior`` → derived ``/company/careers/eng``
        # then each parent step up to ``/``.
        result = probe.build_prefix_ancestry(
            "https://x.com/company/careers/eng/1234-senior"
        )
        assert result == [
            "/company/careers/eng",
            "/company/careers",
            "/company",
            "/",
        ]

    def test_derived_root_collapses_to_single_entry(self, probe: ModuleType) -> None:
        # A single-segment sample derives to ``/``; the loop's dedup must
        # not emit ``["/", "/"]``.
        assert probe.build_prefix_ancestry("/7540236") == ["/"]

    def test_two_segment_derives_to_direct_parent(self, probe: ModuleType) -> None:
        # ``/jobs/7540-eng`` derives to ``/jobs``; parent is ``/``.
        assert probe.build_prefix_ancestry("/jobs/7540-eng") == ["/jobs", "/"]


# ---------------------------------------------------------------------------
# sample_tail_depth
# ---------------------------------------------------------------------------


class TestSampleTailDepth:
    """``sample_tail_depth`` counts segments under a candidate prefix."""

    def test_two_segments_under_prefix(self, probe: ModuleType) -> None:
        # Depth 2: ``eng/1234-senior`` under ``/company/careers``.
        assert (
            probe.sample_tail_depth(
                "https://x.com/company/careers/eng/1234-senior",
                "/company/careers",
            )
            == 2
        )

    def test_prefix_mismatch_returns_zero(self, probe: ModuleType) -> None:
        # Sample does not start with prefix — nothing to count.
        assert probe.sample_tail_depth("https://x.com/apply/abc", "/jobs") == 0

    def test_root_prefix_counts_all_segments(self, probe: ModuleType) -> None:
        # Every path descends from ``/``; count all non-empty segments.
        assert probe.sample_tail_depth("https://x.com/a/b/c", "/") == 3

    def test_trailing_slash_ignored(self, probe: ModuleType) -> None:
        # Trailing slash on the sample must not inflate the count.
        assert probe.sample_tail_depth("https://x.com/jobs/1234/", "/jobs") == 1


# ---------------------------------------------------------------------------
# suggest_min_depth — the C9 heuristic
# ---------------------------------------------------------------------------


class TestSuggestMinDepth:
    """``suggest_min_depth`` reproduces the shipped Databricks / Avionyx guidance."""

    def test_databricks_shape_yields_two(self, probe: ModuleType) -> None:
        # Sample depth 2, chrome at depth 1 → floor of 2 excludes the
        # chrome without cutting the sample's own depth.
        assert probe.suggest_min_depth(2, {1: 5, 2: 10}) == 2

    def test_avionyx_shape_yields_two(self, probe: ModuleType) -> None:
        # Sample depth 3, chrome at depth 1 → shallow_max + 1 = 2, the
        # smallest floor that keeps the depth-3 postings.
        assert probe.suggest_min_depth(3, {1: 3, 3: 16}) == 2

    def test_no_shallow_returns_none(self, probe: ModuleType) -> None:
        # Nothing below the sample depth — no chrome to exclude.
        assert probe.suggest_min_depth(2, {2: 5}) is None

    def test_zero_sample_depth_returns_none(self, probe: ModuleType) -> None:
        # Query-branch boards (sample.path == prefix) never go through
        # the id-in-path branch, so min_depth is not consulted.
        assert probe.suggest_min_depth(0, {1: 5}) is None

    def test_empty_histogram_returns_none(self, probe: ModuleType) -> None:
        # No id-in-path anchors at all — the query bucket is doing the
        # work; suggesting a floor would be nonsensical.
        assert probe.suggest_min_depth(2, {}) is None

    def test_multiple_shallow_depths_picks_max(self, probe: ModuleType) -> None:
        # Chrome at both depth 1 AND depth 2, sample at depth 3 — the
        # floor must be 3 to exclude both shallow clusters.
        assert probe.suggest_min_depth(3, {1: 2, 2: 4, 3: 10}) == 3


# ---------------------------------------------------------------------------
# evaluate_fingerprints
# ---------------------------------------------------------------------------


class TestEvaluateFingerprints:
    """``evaluate_fingerprints`` fires on host / DOM / network axes."""

    def test_greenhouse_host_hit(self, probe: ModuleType) -> None:
        hits = probe.evaluate_fingerprints("acme.job-boards.greenhouse.io", {}, [])
        assert [(h.platform, list(h.signals)) for h in hits] == [
            ("Greenhouse", ["host"])
        ]
        assert hits[0].strategy_suggestion == "greenhouse"

    def test_greenhouse_all_three_axes(self, probe: ModuleType) -> None:
        # Every axis fires: hostname + DOM iframe + boards-api response.
        hits = probe.evaluate_fingerprints(
            "acme.job-boards.greenhouse.io",
            {"[data-gh_jid]": True},
            ["https://boards-api.greenhouse.io/v1/boards/acme/jobs"],
        )
        assert hits[0].platform == "Greenhouse"
        assert list(hits[0].signals) == ["host", "dom", "network"]

    def test_lever_host_hit(self, probe: ModuleType) -> None:
        hits = probe.evaluate_fingerprints("jobs.lever.co", {}, [])
        assert hits[0].platform == "Lever"
        assert hits[0].strategy_suggestion == "dom"  # No Lever adapter yet.

    def test_no_match_returns_empty(self, probe: ModuleType) -> None:
        # A vanilla marketing hostname with no DOM/network signals.
        hits = probe.evaluate_fingerprints("careers.example.com", {}, [])
        assert hits == []

    def test_only_greenhouse_maps_to_greenhouse_strategy(
        self, probe: ModuleType
    ) -> None:
        """Every non-Greenhouse fingerprint suggests ``dom``; the plan is explicit."""
        for fp in probe.FINGERPRINT_TABLE:
            if fp.platform == "Greenhouse":
                assert fp.strategy_suggestion == "greenhouse"
            else:
                assert fp.strategy_suggestion == "dom", (
                    f"{fp.platform} unexpectedly maps to {fp.strategy_suggestion!r}"
                )


# ---------------------------------------------------------------------------
# find_unclassified_job_apis
# ---------------------------------------------------------------------------


class TestFindUnclassifiedJobApis:
    """``find_unclassified_job_apis`` filters out attributed APIs."""

    def test_greenhouse_api_suppressed_when_greenhouse_fired(
        self, probe: ModuleType
    ) -> None:
        # The Greenhouse fingerprint claims ``boards-api.greenhouse.io``,
        # so surfacing it again as "unclassified" would double-report.
        hit = probe.FingerprintHit(
            platform="Greenhouse",
            signals=("host",),
            strategy_suggestion="greenhouse",
        )
        urls = ["https://boards-api.greenhouse.io/v1/boards/acme/jobs"]
        assert probe.find_unclassified_job_apis(urls, [hit]) == []

    def test_generic_job_api_surfaced(self, probe: ModuleType) -> None:
        # Path contains "jobs"; no fingerprint claims the host — this is
        # exactly the G4 signal we want to elevate to the human.
        urls = ["https://api.example.com/v2/careers/list"]
        assert probe.find_unclassified_job_apis(urls, []) == [urls[0]]

    def test_non_job_path_ignored(self, probe: ModuleType) -> None:
        # A tracking / analytics URL — even if the host contains "jobs",
        # scoping to the path avoids the false positive.
        urls = ["https://jobs-analytics.example.com/track"]
        assert probe.find_unclassified_job_apis(urls, []) == []

    def test_dedup_preserves_first_occurrence(self, probe: ModuleType) -> None:
        # The same job-API URL fired twice — surface it once, in order.
        url = "https://api.example.com/v1/openings"
        assert probe.find_unclassified_job_apis([url, url], []) == [url]


# ---------------------------------------------------------------------------
# build_suggestion
# ---------------------------------------------------------------------------


class TestBuildSuggestion:
    """``build_suggestion`` composes the section-7 payload."""

    def _default_kwargs(self) -> dict[str, object]:
        return {
            "company_snippet_name": "Acme",
            "job_board_url": "https://acme.com/careers",
            "sample_job_url": "https://acme.com/jobs/123",
            "derived_prefix": "/jobs",
            "min_depth_suggestion": None,
            "min_depth_prefix": None,
            "fingerprint_hits": [],
            "paginate_found": False,
            "hidden_heavy": False,
            "zero_anchor_with_api": False,
            "cross_origin_frame_with_anchors": False,
            "filter_findings_summary": None,
        }

    def test_default_snippet_has_minimal_shape(self, probe: ModuleType) -> None:
        sug = probe.build_suggestion(**self._default_kwargs())
        assert sug["strategy"] == "dom"
        assert "link_rule" not in sug["company_snippet"]
        assert "paginate" not in sug["company_snippet"]
        # Standing caveat is always present.
        assert any("renders the board once" in c for c in sug["caveats"])

    def test_greenhouse_snippet(self, probe: ModuleType) -> None:
        kwargs = self._default_kwargs()
        kwargs["fingerprint_hits"] = [
            probe.FingerprintHit(
                platform="Greenhouse",
                signals=("host",),
                strategy_suggestion="greenhouse",
            )
        ]
        sug = probe.build_suggestion(**kwargs)
        assert sug["strategy"] == "greenhouse"
        assert 'strategy="greenhouse"' in sug["company_snippet"]
        # DOM-only knobs must NOT leak into a greenhouse snippet.
        assert "link_rule" not in sug["company_snippet"]
        assert "paginate" not in sug["company_snippet"]
        # Notes mention the API-payload workflow explicitly.
        assert any("tests/fixtures/api/greenhouse" in n for n in sug["notes"])

    def test_min_depth_lands_in_snippet(self, probe: ModuleType) -> None:
        kwargs = self._default_kwargs()
        kwargs["min_depth_suggestion"] = 2
        # min_depth_prefix omitted / None → treated as "same as derived",
        # which is the bare-min_depth form.
        sug = probe.build_suggestion(**kwargs)
        assert "LinkRule(min_depth=2)" in sug["company_snippet"]
        assert "path_prefix" not in sug["company_snippet"]
        assert sug["min_depth"] == 2

    def test_min_depth_same_as_derived_prefix_omits_path_prefix(
        self, probe: ModuleType
    ) -> None:
        # Explicitly passing min_depth_prefix == derived_prefix must
        # behave identically to the bare form — the LinkRule does not
        # need a path_prefix override when the auto-derivation is
        # already correct.
        kwargs = self._default_kwargs()
        kwargs["min_depth_suggestion"] = 2
        kwargs["min_depth_prefix"] = kwargs["derived_prefix"]
        sug = probe.build_suggestion(**kwargs)
        assert "LinkRule(min_depth=2)" in sug["company_snippet"]
        assert "path_prefix" not in sug["company_snippet"]

    def test_min_depth_on_promoted_ancestor_emits_path_prefix(
        self, probe: ModuleType
    ) -> None:
        # C9 ancestor-promotion (Databricks / Avionyx shape): the sample
        # URL derives a too-narrow prefix but the depth split lives at a
        # wider ancestor. The emitted LinkRule must pin path_prefix to
        # the ancestor — otherwise the runtime posixpath.dirname would
        # re-derive the narrow prefix and lose the depth fix.
        kwargs = self._default_kwargs()
        kwargs["derived_prefix"] = "/company/careers/it"
        kwargs["min_depth_suggestion"] = 2
        kwargs["min_depth_prefix"] = "/company/careers"
        sug = probe.build_suggestion(**kwargs)
        assert (
            'LinkRule(path_prefix="/company/careers", min_depth=2)'
            in sug["company_snippet"]
        )
        # The C9 note now reflects the promoted scope, not "derived prefix".
        assert any("/company/careers" in n for n in sug["notes"])

    def test_paginate_lands_in_snippet(self, probe: ModuleType) -> None:
        kwargs = self._default_kwargs()
        kwargs["paginate_found"] = True
        sug = probe.build_suggestion(**kwargs)
        assert "paginate=True" in sug["company_snippet"]
        assert sug["paginate"] is True

    def test_greenhouse_suppresses_paginate(self, probe: ModuleType) -> None:
        # Zscaler-shape regression: a Greenhouse-hosted board renders a
        # "Next page" pager on the DOM listing, but the API strategy
        # ignores it. Emitting paginate=True on a greenhouse Company
        # entry would be contradictory (the DOM path is bypassed
        # entirely) and confusing to a reader. The paginate note must
        # also be dropped for the same reason.
        kwargs = self._default_kwargs()
        kwargs["fingerprint_hits"] = [
            probe.FingerprintHit(
                platform="Greenhouse",
                signals=("host",),
                strategy_suggestion="greenhouse",
            )
        ]
        kwargs["paginate_found"] = True
        sug = probe.build_suggestion(**kwargs)
        assert "paginate=True" not in sug["company_snippet"]
        assert not any(
            "paginate=True" in n or "capture the snapshot with --paginate" in n
            for n in sug["notes"]
        )
        # But the raw report field still records the observation — the
        # human reading section 4 should see the pager was found.
        assert sug["paginate"] is True

    def test_note_bullets_track_findings(self, probe: ModuleType) -> None:
        kwargs = self._default_kwargs()
        kwargs["paginate_found"] = True
        kwargs["min_depth_suggestion"] = 2
        kwargs["cross_origin_frame_with_anchors"] = True
        kwargs["hidden_heavy"] = True
        kwargs["zero_anchor_with_api"] = True
        kwargs["filter_findings_summary"] = "1 native <select>(s)"
        sug = probe.build_suggestion(**kwargs)
        note_text = "\n".join(sug["notes"])
        # Every signal we lit gets its own note.
        assert "paginate=True" in note_text
        assert "min_depth" in note_text
        assert "cross-origin" in note_text.lower()
        assert "hidden-anchor" in note_text.lower()
        assert "JSON job API" in note_text or "job API" in note_text
        assert "filter-control census" in note_text.lower()


# ---------------------------------------------------------------------------
# Fingerprint table sanity
# ---------------------------------------------------------------------------


class TestFingerprintTable:
    """Static properties of ``FINGERPRINT_TABLE`` we rely on downstream."""

    def test_greenhouse_first_entry(self, probe: ModuleType) -> None:
        # Deterministic order matters: the suggestion assembly looks up
        # Greenhouse specifically by platform name, but consumers may
        # rely on table order for stable JSON output.
        assert probe.FINGERPRINT_TABLE[0].platform == "Greenhouse"

    def test_every_entry_has_at_least_one_signal_axis(self, probe: ModuleType) -> None:
        # A fingerprint with no signals on any axis would never fire.
        for fp in probe.FINGERPRINT_TABLE:
            assert fp.host_patterns or fp.dom_selectors or fp.network_patterns, (
                f"{fp.platform} has no signals on any axis"
            )

    def test_location_tokens_include_c3_prompt_additions(
        self, probe: ModuleType
    ) -> None:
        # The C3 clause taught the agent about ``pais`` / ``país``
        # and ``city`` / ``region``. If those disappear from the probe's
        # token list, boards the agent recognises will silently drop out
        # of the filter-census output.
        assert "pais" in probe.LOCATION_TOKENS
        assert "país" in probe.LOCATION_TOKENS
        assert "city" in probe.LOCATION_TOKENS
        assert "region" in probe.LOCATION_TOKENS
