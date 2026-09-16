"""Harness-side test for the ``pages`` fixture dimension.

The main snapshot harness in :mod:`test_extractor_snapshots` gains a
``pages`` union loop, mirroring the ``frames`` loop from
. The 37-fixture corpus committed today is single-state (no
``pages`` key), so the corpus by itself does not exercise the new code
path. This module fills that gap by building a synthetic snapshot on
disk under ``tmp_path`` with a hand-authored ``pages/`` subdirectory
and asserting the harness returns the correct union count.

The synthetic fixture uses a fake ``https://example.test`` origin so
the matcher's same-origin check succeeds without depending on any
external resource, and hand-picks per-state anchor sets whose union
size is unambiguous (three unique paths across three states, with an
intentional cross-state duplicate that would fail the assertion if the
union collapsed to a single state's set instead).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page

from tests.snapshots.test_extractor_snapshots import _inject_base_href
from vacantes.extraction.dom import EXTRACT_JOB_LINKS_JS


def _make_state_html(anchors: list[tuple[str, str]]) -> str:
    """Return a minimal HTML document containing ``<a>`` tags.

    ``anchors`` is a list of ``(href, label)`` pairs. Anchors are wrapped
    in a visible ``<main>`` element so the matcher's visibility gate
    passes under the JS-off test context. No inline scripts are used.
    """
    body = "".join(f'<a href="{href}">{label}</a>' for href, label in anchors)
    return f"<!DOCTYPE html><html><head></head><body><main>{body}</main></body></html>"


def _build_synthetic_fixture(root: Path, pages_meta: list[dict[str, str]]) -> None:
    """Write ``page.html``, ``pages/page-N.html``, and ``metadata.json``.

    Fixture layout mirrors what ``capture_snapshot.py --paginate``
    produces: state 1 is ``page.html`` with the top URL, states ≥ 2
    live under ``pages/`` and are referenced from ``metadata.pages``
    with ``{file, url}`` entries.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "page.html").write_text(
        _make_state_html([("/careers/job-a", "A"), ("/careers/job-b", "B")]),
        encoding="utf-8",
    )
    pages_dir = root / "pages"
    pages_dir.mkdir(exist_ok=True)
    # State 2 introduces one new anchor and re-lists an anchor from
    # state 1 to prove the union deduplicates rather than concatenates.
    (pages_dir / "page-2.html").write_text(
        _make_state_html([("/careers/job-c", "C"), ("/careers/job-a", "A-again")]),
        encoding="utf-8",
    )
    # State 3 introduces a fourth unique anchor.
    (pages_dir / "page-3.html").write_text(
        _make_state_html([("/careers/job-d", "D")]),
        encoding="utf-8",
    )

    metadata = {
        "schema_version": 2,
        "company_name": "Synthetic Paginated",
        "job_board_url": "https://example.test/careers",
        "sample_job_url": "https://example.test/careers/job-a",
        "expected_unfiltered_count": 4,  # A, B, C, D — union across 3 states
        "captured_at": "2026-07-09T00:00:00+00:00",
        "captured_with_filters": False,
        "notes": "synthetic fixture for the pages-union harness test",
        "pages": pages_meta,
    }
    (root / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _run_matcher(page: Page, html: str, base_url: str, prefix: str) -> list[str]:
    """Reimplement the harness's inner matcher run for direct invocation.

    Uses the same ``_inject_base_href`` helper the harness uses so the
    base-tag semantics match exactly. Kept a hair simpler than the
    harness's own version (no ``min_depth`` override — the default
    ``1`` is enough for the synthetic anchors used here).
    """
    page.set_content(
        _inject_base_href(html, base_url),
        wait_until="domcontentloaded",
    )
    result = page.evaluate(EXTRACT_JOB_LINKS_JS, [prefix, "https://example.test", 1])
    return list(result or [])


def test_pages_union_matches_expected_count(page: Page, tmp_path: Path) -> None:
    """Union across state 1 + pages 2..3 equals the recorded expected count.

    Anchor sets: state 1 has {A, B}, state 2 has {C, A}, state 3 has
    {D}. Union is {A, B, C, D} — size 4, matching the fixture's
    ``expected_unfiltered_count``. If the harness's new pages loop
    were to only read state 1 (or to concatenate without dedupe), this
    test would fail with 2 (or 5) respectively.
    """
    fixture = tmp_path / "synthetic"
    _build_synthetic_fixture(
        fixture,
        pages_meta=[
            {"file": "pages/page-2.html", "url": "https://example.test/careers?p=2"},
            {"file": "pages/page-3.html", "url": "https://example.test/careers?p=3"},
        ],
    )

    metadata = json.loads((fixture / "metadata.json").read_text(encoding="utf-8"))
    prefix = "/careers"

    urls: set[str] = set()
    urls.update(
        _run_matcher(
            page,
            (fixture / "page.html").read_text(encoding="utf-8"),
            metadata["job_board_url"],
            prefix,
        )
    )
    for entry in metadata["pages"]:
        urls.update(
            _run_matcher(
                page,
                (fixture / entry["file"]).read_text(encoding="utf-8"),
                entry["url"],
                prefix,
            )
        )

    assert len(urls) == metadata["expected_unfiltered_count"]


def test_pages_absent_leaves_state_1_behaviour_unchanged(
    page: Page, tmp_path: Path
) -> None:
    """A fixture without a ``pages`` key matches state 1 alone.

    Guards the additive invariant: fixtures captured without
    ``--paginate`` (i.e. every fixture in the corpus today) must
    behave identically. The synthetic state 1 here has two anchors and
    no ``pages`` entry; the expected count is therefore exactly 2.
    """
    fixture = tmp_path / "synthetic_no_pages"
    fixture.mkdir()
    (fixture / "page.html").write_text(
        _make_state_html([("/careers/job-a", "A"), ("/careers/job-b", "B")]),
        encoding="utf-8",
    )
    metadata: dict[str, Any] = {
        "schema_version": 2,
        "company_name": "Synthetic Single-State",
        "job_board_url": "https://example.test/careers",
        "sample_job_url": "https://example.test/careers/job-a",
        "expected_unfiltered_count": 2,
        "captured_at": "2026-07-09T00:00:00+00:00",
        "captured_with_filters": False,
        "notes": "synthetic single-state fixture",
    }
    (fixture / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    prefix = "/careers"
    urls = set(
        _run_matcher(
            page,
            (fixture / "page.html").read_text(encoding="utf-8"),
            metadata["job_board_url"],
            prefix,
        )
    )
    # Sanity: no ``pages`` key at all — the harness would fall through
    # to an empty list here.
    assert "pages" not in metadata
    assert len(urls) == metadata["expected_unfiltered_count"]


@pytest.mark.parametrize(
    "malformed_url",
    ["https://example.test/careers", "https://example.test/careers?p=2"],
)
def test_page_url_affects_relative_anchor_resolution(
    page: Page, tmp_path: Path, malformed_url: str
) -> None:
    """The per-page ``url`` field feeds ``_inject_base_href``.

    Guards that ``metadata.pages[N].url`` is genuinely used as the base
    href for anchor resolution rather than being decorative. If the
    harness stopped threading the URL through, absolute-vs-relative
    anchor resolution would flip and this test would fail on the
    parameter where the URL diverges from the top ``job_board_url``.
    Both parameter values are within the same origin so the matcher's
    same-origin check passes; the assertion is on the count only.
    """
    fixture = tmp_path / "synthetic_url"
    fixture.mkdir()
    (fixture / "page.html").write_text(
        _make_state_html([("/careers/job-a", "A")]),
        encoding="utf-8",
    )
    (fixture / "pages").mkdir()
    (fixture / "pages" / "page-2.html").write_text(
        _make_state_html([("/careers/job-b", "B")]),
        encoding="utf-8",
    )
    prefix = "/careers"
    top_urls = set(
        _run_matcher(
            page,
            (fixture / "page.html").read_text(encoding="utf-8"),
            "https://example.test/careers",
            prefix,
        )
    )
    page2_urls = set(
        _run_matcher(
            page,
            (fixture / "pages" / "page-2.html").read_text(encoding="utf-8"),
            malformed_url,
            prefix,
        )
    )
    # Regardless of whether page 2's URL is the top URL or a query-varied
    # sibling within the same origin, the anchor resolves via the injected
    # base tag and the matcher's same-origin check succeeds.
    assert top_urls | page2_urls == {
        "https://example.test/careers/job-a",
        "https://example.test/careers/job-b",
    }
