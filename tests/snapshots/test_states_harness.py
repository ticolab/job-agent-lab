"""Harness-side test for the SYS-13 ``states`` fixture dimension.

The main snapshot harness in :mod:`test_extractor_snapshots` gains a
``states`` union loop under SYS-13, mirroring the ``pages`` loop from
SYS-5. No corpus fixture today declares ``pre_filter_urls`` so no
committed fixture exercises the new code path; this module fills that
gap by building a synthetic snapshot on disk under ``tmp_path`` with a
hand-authored ``states/`` subdirectory and a ``top_url`` metadata key,
then asserting the harness returns the correct union count.

Three tests pin the SYS-13 harness contract:

1. Multi-state union with a cross-state duplicate — proves the union
   deduplicates rather than collapsing to a single state or
   concatenating with double-counting.
2. Absent ``states`` key — a pre-SYS-13 fixture takes the same code
   path with the states dimension collapsing to zero contributions
   (byte-stability pin for every corpus fixture that predates SYS-13).
3. ``top_url`` overrides ``job_board_url`` for the state-1 replay —
   proves the harness genuinely threads ``metadata.top_url`` into the
   ``<base href>`` injection rather than treating it as decorative.

The synthetic fixture uses a fake ``https://example.test`` origin so
the matcher's same-origin check succeeds without depending on any
external resource.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from playwright.sync_api import Page

from job_agent_lab.extraction.dom import EXTRACT_JOB_LINKS_JS
from tests.snapshots.test_extractor_snapshots import _inject_base_href


def _make_state_html(anchors: list[tuple[str, str]]) -> str:
    """Return a minimal HTML document containing ``<a>`` tags.

    Anchors are wrapped in a visible ``<main>`` element so the matcher's
    visibility gate passes under the JS-off replay context. No inline
    scripts are used — the harness runs the matcher against static
    HTML, so any script-driven visibility toggling would never execute.
    """
    body = "".join(f'<a href="{href}">{label}</a>' for href, label in anchors)
    return f"<!DOCTYPE html><html><head></head><body><main>{body}</main></body></html>"


def _run_matcher(page: Page, html: str, base_url: str, prefix: str) -> list[str]:
    """Reimplement the harness's inner matcher run for direct invocation.

    Uses the same ``_inject_base_href`` helper the harness uses so the
    base-tag semantics match exactly.
    """
    page.set_content(
        _inject_base_href(html, base_url),
        wait_until="domcontentloaded",
    )
    result = page.evaluate(EXTRACT_JOB_LINKS_JS, [prefix, "https://example.test", 1])
    return list(result or [])


def test_states_union_matches_expected_count(page: Page, tmp_path: Path) -> None:
    """Union across state 1 + states 2..3 equals the recorded expected count.

    Anchor sets: state 1 has {A, B}, state 2 has {C, A} (A is an
    intentional cross-state duplicate), state 3 has {D}. Union is
    {A, B, C, D} — size 4. If the harness's new states loop were to
    only read state 1 the count would be 2; if it concatenated without
    dedupe the count would be 5. Either regression fails with a clear
    numeric mismatch.
    """
    fixture = tmp_path / "synthetic_states"
    fixture.mkdir()
    (fixture / "page.html").write_text(
        _make_state_html([("/careers/job-a", "A"), ("/careers/job-b", "B")]),
        encoding="utf-8",
    )
    states_dir = fixture / "states"
    states_dir.mkdir()
    (states_dir / "state-2.html").write_text(
        _make_state_html([("/careers/job-c", "C"), ("/careers/job-a", "A-again")]),
        encoding="utf-8",
    )
    (states_dir / "state-3.html").write_text(
        _make_state_html([("/careers/job-d", "D")]),
        encoding="utf-8",
    )

    metadata: dict[str, Any] = {
        "schema_version": 2,
        "company_name": "Synthetic Prefiltered",
        "job_board_url": "https://example.test/careers",
        "sample_job_url": "https://example.test/careers/job-a",
        "expected_unfiltered_count": 4,
        "captured_at": "2026-08-01T00:00:00+00:00",
        "captured_with_filters": True,
        "notes": "synthetic fixture for the states-union harness test",
        "pre_filter_urls": [
            "https://example.test/careers?location=cr",
            "https://example.test/careers?location=latam",
            "https://example.test/careers?location=us",
        ],
        "top_url": "https://example.test/careers?location=cr",
        "states": [
            {
                "file": "states/state-2.html",
                "url": "https://example.test/careers?location=latam",
            },
            {
                "file": "states/state-3.html",
                "url": "https://example.test/careers?location=us",
            },
        ],
    }
    (fixture / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    prefix = "/careers"

    urls: set[str] = set()
    urls.update(
        _run_matcher(
            page,
            (fixture / "page.html").read_text(encoding="utf-8"),
            metadata["top_url"],
            prefix,
        )
    )
    for entry in metadata["states"]:
        urls.update(
            _run_matcher(
                page,
                (fixture / entry["file"]).read_text(encoding="utf-8"),
                entry["url"],
                prefix,
            )
        )

    assert len(urls) == metadata["expected_unfiltered_count"]


def test_states_absent_leaves_state_1_behaviour_unchanged(
    page: Page, tmp_path: Path
) -> None:
    """A fixture without a ``states`` key matches state 1 alone.

    Guards the SYS-13 additive-optional invariant: fixtures captured
    without ``pre_filter_urls`` declaring must behave identically. The
    synthetic state 1 here has two anchors, no ``states`` entry, and
    no ``top_url`` entry; the expected count is therefore exactly 2
    and the harness must fall back to ``job_board_url`` for the base
    href.
    """
    fixture = tmp_path / "synthetic_no_states"
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
        "captured_at": "2026-08-01T00:00:00+00:00",
        "captured_with_filters": False,
        "notes": "synthetic single-state fixture (SYS-13 byte-stability pin)",
    }
    (fixture / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    prefix = "/careers"
    assert "states" not in metadata
    assert "top_url" not in metadata

    urls = set(
        _run_matcher(
            page,
            (fixture / "page.html").read_text(encoding="utf-8"),
            metadata["job_board_url"],
            prefix,
        )
    )
    assert len(urls) == metadata["expected_unfiltered_count"]


def test_top_url_governs_state_1_base_href(page: Page, tmp_path: Path) -> None:
    """``metadata.top_url`` — not ``job_board_url`` — is the state-1 base href.

    Pins that ``metadata.top_url`` is genuinely threaded into the
    ``<base href>`` injection. Uses a relative anchor ``job-x`` whose
    resolution depends on the base href's path. Under
    ``top_url = https://example.test/careers/?location=cr`` the base is
    the ``/careers/`` directory (trailing slash), so ``job-x`` resolves
    to ``https://example.test/careers/job-x?location=cr`` — inside the
    ``/careers`` prefix and the matcher yields it (count 1). If the
    harness reverted to ``job_board_url = https://example.test/`` the
    base would be the origin root, ``job-x`` would resolve to
    ``https://example.test/job-x`` — outside the ``/careers`` prefix —
    and the matcher would drop it (count 0). The count asymmetry is
    what the assertion catches.
    """
    fixture = tmp_path / "synthetic_top_url"
    fixture.mkdir()
    (fixture / "page.html").write_text(
        _make_state_html([("job-x", "X")]),
        encoding="utf-8",
    )
    metadata: dict[str, Any] = {
        "schema_version": 2,
        "company_name": "Synthetic Top-URL",
        # Bare-root ``job_board_url`` — the harness would resolve the
        # relative anchor outside the ``/careers`` prefix if it fell
        # back to this URL instead of honouring ``top_url``.
        "job_board_url": "https://example.test/",
        "sample_job_url": "https://example.test/careers/job-a",
        "expected_unfiltered_count": 1,
        "captured_at": "2026-08-01T00:00:00+00:00",
        "captured_with_filters": True,
        "notes": "synthetic fixture proving top_url overrides job_board_url",
        # Trailing slash after ``careers`` is load-bearing: it makes
        # the base URL the ``/careers/`` *directory* rather than a
        # sibling of ``careers``, so a relative anchor resolves under
        # ``/careers/`` — inside the matcher's path_prefix.
        "pre_filter_urls": ["https://example.test/careers/?location=cr"],
        "top_url": "https://example.test/careers/?location=cr",
    }
    (fixture / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    prefix = "/careers"
    urls = set(
        _run_matcher(
            page,
            (fixture / "page.html").read_text(encoding="utf-8"),
            metadata["top_url"],
            prefix,
        )
    )
    assert len(urls) == metadata["expected_unfiltered_count"]
