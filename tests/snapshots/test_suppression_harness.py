"""Harness-side test for the SYS-14 ``suppress_ancestor_selector`` key.

``tests/snapshots/test_extractor_snapshots.py`` gains a fourth matcher
argument sourced from ``metadata.get("suppress_ancestor_selector")``. The
corpus committed today is entirely pre-SYS-14 (no fixture sets the key),
so the corpus alone never exercises the new branch. This module fills
that gap.

Unlike the sibling ``frames`` / ``pages`` / ``states`` harness tests —
which re-implement the union loop inline — the tests here call the
**real** harness function
(:func:`test_extractor_matches_expected_count`) against a synthetic
fixture written to ``tmp_path``. That is deliberate: the property under
test is not "the matcher suppresses" (``test_matcher_rules.py`` owns
that) but "the harness *reads the key and passes it through*". Only
driving the production function proves the wiring; an inline
re-implementation would pass even if the harness ignored the key
entirely.

The fixture mirrors the C16 shape that motivates SYS-14: a Ulteig-style
"Featured opportunities" container whose anchors are indistinguishable
from the real postings at the URL layer (same origin, same prefix, same
depth), so only the container gate can separate them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page

from tests.snapshots.test_extractor_snapshots import (
    test_extractor_matches_expected_count as run_harness,
)

_ORIGIN = "https://example.test"
_SUPPRESS = '[data-automation="featured-opportunities"]'

# Two real postings plus three "featured" recommendations. Every anchor
# shares the origin, the ``/careers`` prefix, and depth 1 — there is no
# URL-layer discriminator, which is the defining property of C16.
_PAGE_HTML = (
    "<!DOCTYPE html><html><head></head><body><main>"
    '<a href="/careers/real-1">Real 1</a>'
    '<a href="/careers/real-2">Real 2</a>'
    '<div data-automation="featured-opportunities">'
    '<a href="/careers/featured-1">Featured 1</a>'
    '<a href="/careers/featured-2">Featured 2</a>'
    '<a href="/careers/featured-3">Featured 3</a>'
    "</div>"
    "</main></body></html>"
)


def _write_fixture(root: Path, *, suppress: str | None, expected: int) -> Path:
    """Write a one-document synthetic fixture; return its directory."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "page.html").write_text(_PAGE_HTML, encoding="utf-8")

    metadata: dict[str, Any] = {
        "schema_version": 2,
        "company_name": "Synthetic Suppression",
        "job_board_url": f"{_ORIGIN}/careers",
        "sample_job_url": f"{_ORIGIN}/careers/real-1",
        "expected_unfiltered_count": expected,
        "captured_at": "2026-08-02T00:00:00+00:00",
        "captured_with_filters": True,
        "notes": "synthetic fixture for the SYS-14 suppression harness test",
    }
    # Additive-optional, exactly as ``_write_snapshot`` emits it.
    if suppress is not None:
        metadata["suppress_ancestor_selector"] = suppress

    (root / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return root


def test_harness_applies_recorded_suppression_selector(
    page: Page, tmp_path: Path
) -> None:
    """A fixture recording the key replays with the suppressed section excluded.

    Five anchors are present in the frozen HTML; three live inside the
    featured container. With the key recorded the harness must count
    exactly the two real postings. If the harness dropped the key the
    matcher would return all five and this fails with a 5-vs-2
    mismatch — which is precisely the silent over-count a
    suppression-active fixture would otherwise suffer on every run.
    """
    fixture = _write_fixture(tmp_path / "suppressed", suppress=_SUPPRESS, expected=2)
    run_harness(fixture, page)


def test_same_fixture_without_the_key_counts_every_anchor(
    page: Page, tmp_path: Path
) -> None:
    """The control: identical HTML, no key → all five anchors counted.

    This is the byte-stability half of the contract. It pins that the
    harness's default (key absent → ``None`` → gate disabled) is what
    every pre-SYS-14 fixture relies on, and it proves the previous
    test's result comes from the recorded selector rather than from
    something incidental in the markup.
    """
    fixture = _write_fixture(tmp_path / "plain", suppress=None, expected=5)
    run_harness(fixture, page)


def test_recorded_selector_mismatch_is_a_loud_failure(
    page: Page, tmp_path: Path
) -> None:
    """A fixture whose recorded count ignores its own selector fails.

    Guards against the fixture-authoring mistake the SYS-14 fixture
    policy exists to prevent: capturing Ulteig *without* suppression
    (recording the drifting featured section into the count) and then
    adding the selector afterwards. The harness must not paper over the
    inconsistency.
    """
    fixture = _write_fixture(
        # Selector active, but the count was recorded as if it were not.
        tmp_path / "inconsistent",
        suppress=_SUPPRESS,
        expected=5,
    )
    with pytest.raises(AssertionError, match="matcher returned 2 links"):
        run_harness(fixture, page)
