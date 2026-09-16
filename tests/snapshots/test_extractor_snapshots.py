"""Snapshot regression tests for the deterministic job-link extractor.

Each subdirectory under ``tests/fixtures/snapshots/<slug>/`` represents one
integrated company and contains at least two files: ``page.html`` (the
frozen rendered DOM of the company's listing page at capture time) and
``metadata.json`` (company identifiers plus the expected unfiltered job
count). Schema-v2 fixtures may additionally contain a ``frames/``
subdirectory with one HTML file per captured same-origin frame; each frame
is recorded in ``metadata.frames`` as ``{file, url}``. adds an
optional ``pages/`` subdirectory carrying later paginated DOM states
(``pages/page-N.html`` for N ≥ 2) with a matching ``metadata.pages`` list
of ``{file, url}`` entries; only fixtures captured with
``capture_snapshot.py --paginate`` emit this key. adds an optional
``states/`` subdirectory carrying the pre-filter-URL states of an
agent-less multi-state fixture (``states/state-N.html`` for N ≥ 2), a
matching ``metadata.states`` list of ``{file, url}`` entries, and a
``metadata.top_url`` key recording the URL state 1 was rendered from
(equal to ``pre_filter_urls[0]``, not ``job_board_url`` — the runtime
never visits ``job_board_url`` on the prefiltered path). Only fixtures whose
``Company`` entry declares non-empty ``pre_filter_urls`` emit these
three keys. At collection time this module discovers every well-formed
snapshot directory and parametrizes a single test function over them.

For each snapshot the test runs the production matcher JavaScript
(``EXTRACT_JOB_LINKS_JS``, imported from ``vacantes.extraction.dom``)
against the frozen top-document HTML inside a real Chromium page, then repeats
the matcher run against each captured frame document, each captured
pagination-state document, and each captured pre-filter-URL state
(each loaded under its own recorded base href). The href sets returned by
every run are unioned and compared against
``expected_unfiltered_count`` — mirroring the runtime behaviour where the
matcher walks same-origin frames itself in-page, the pagination walker unions
matches across pagination states when ``Company.paginate=True``, and the
 prefiltered path unions matches across each declared URL when
``Company.pre_filter_urls`` is non-empty.

v1 fixtures (no ``frames``, ``pages``, or ``states`` keys) take the exact
same code path with the absent dimensions collapsing to zero
contributions, so no v1, or snapshot moves as a
result of the additions. Because the matcher source is imported
directly from the production module, there is no risk of the test drifting
out of sync with the live extractor.

Snapshots capture the unfiltered listing-page state. The location-filtered
integration target (e.g. Costa Rica-only, LATAM-only) is validated separately
by the end-to-end run that concludes each integration; conflating the two in
a single test would make failures hard to localise.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Page

from vacantes.extraction.dom import EXTRACT_JOB_LINKS_JS
from vacantes.extraction.dom.rules import derive_path_prefix

SNAPSHOTS_DIR = Path(__file__).parent.parent / "fixtures" / "snapshots"


def _discover_snapshots() -> list[Path]:
    """Return every well-formed snapshot directory under SNAPSHOTS_DIR.

    A directory is considered well-formed when it contains both
    ``page.html`` and ``metadata.json``. The ``.gitkeep`` placeholder and
    any malformed directories are silently skipped, so adding a new
    snapshot is a pure file-create operation requiring no test-code
    changes.
    """
    if not SNAPSHOTS_DIR.is_dir():
        return []
    return sorted(
        p
        for p in SNAPSHOTS_DIR.iterdir()
        if p.is_dir()
        and (p / "page.html").is_file()
        and (p / "metadata.json").is_file()
    )


_SNAPSHOTS = _discover_snapshots()

# When the corpus is empty (Phase 1 ships with zero snapshots), register a
# single skipped placeholder so pytest reports a green run instead of the
# "no tests collected" exit status that would otherwise break the pre-commit
# hook. Phase 2 backfills the corpus, after which this branch is never taken.
_PARAMS: list[Any] = (
    [pytest.param(s, id=s.name) for s in _SNAPSHOTS]
    if _SNAPSHOTS
    else [
        pytest.param(
            None,
            id="no_snapshots_yet",
            marks=pytest.mark.skip(
                reason="No snapshots yet — Phase 2 backfills the corpus."
            ),
        )
    ]
)


def _inject_base_href(html: str, base_url: str) -> str:
    """Inject a ``<base href>`` tag so relative anchors resolve correctly.

    ``page.set_content`` defaults the document base URI to ``about:blank``,
    which causes relative ``href`` attributes to resolve against an opaque
    origin and breaks the matcher's same-origin check. Injecting an explicit
    ``<base>`` tag at the very start of ``<head>`` overrides this.

    Important: the injection is **not** suppressed when the snapshot already
    contains its own ``<base>`` tag. Some sites (e.g. Akurey's Next.js
    bundle) ship a ``<base href="/">`` intended for resolution against the
    real document origin; carried into a synthetic ``set_content`` context
    that resolves to ``about:blank/`` and breaks the matcher. Per the HTML
    spec the first ``<base>`` in document order wins, so inserting ours at
    the start of ``<head>`` deterministically takes precedence over any
    later one the page may carry.
    """
    base_tag = f'<base href="{base_url}">'
    lower = html.lower()
    head_open = lower.find("<head")
    if head_open == -1:
        return f"<head>{base_tag}</head>{html}"
    close_angle = html.find(">", head_open)
    if close_angle == -1:
        return f"<head>{base_tag}</head>{html}"
    return html[: close_angle + 1] + base_tag + html[close_angle + 1 :]


@pytest.mark.parametrize("snapshot_dir", _PARAMS)
def test_extractor_matches_expected_count(
    snapshot_dir: Path | None,
    page: Page,
) -> None:
    """Assert the matcher returns the snapshot's recorded unfiltered count.

    For v2 fixtures the returned URL sets from the top document and every
    recorded frame document are unioned before the count comparison —
    mirroring the runtime's in-page frame walk. The paginated-fixture flow
    adds an additional per-``pages`` union on top of that: fixtures
    captured with ``--paginate`` carry ``pages/page-N.html`` for each
    successive pagination state N ≥ 2, and the matcher runs against each
    state under its own recorded base href. Fixtures without either key
    (v1) or with only ``frames`` (default) take the same union code path
    with the absent dimensions collapsing to zero contributions.

    Failures include the company name, the actual-vs-expected diff, and
    the number of documents unioned so any regression localises to a
    single line of pytest output.
    """
    # The None branch only fires under the empty-corpus sentinel above,
    # which is also marked ``skip`` — the assertion below is defensive.
    assert snapshot_dir is not None

    metadata: dict[str, Any] = json.loads(
        (snapshot_dir / "metadata.json").read_text(encoding="utf-8")
    )

    job_board_url: str = metadata["job_board_url"]
    sample_job_url: str = metadata["sample_job_url"]
    expected: int = metadata["expected_unfiltered_count"]

    parsed = urlparse(job_board_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    # Honour the same override the runtime uses: when the Company entry
    # (and therefore the captured metadata) supplies path_prefix explicitly,
    # skip derivation entirely.
    override: str | None = metadata.get("path_prefix")
    prefix = override if override is not None else derive_path_prefix(sample_job_url)
    # ``min_depth`` is an additive-optional key introduced; v1
    # fixtures and default-floor v2 fixtures omit it, and the fallback of
    # 1 leaves matcher behaviour byte-identical for those.
    min_depth: int = metadata.get("min_depth", 1)
    # ``suppress_ancestor_selector`` is another additive-optional
    # key: absent on every legacy fixture, where ``None`` disables the
    # matcher's suppression gate. Fixtures captured with suppression active
    # (Ulteig / C16) must replay under the same selector, otherwise the
    # replay would count the suppressed section the capture excluded.
    suppress_selector: str | None = metadata.get("suppress_ancestor_selector")

    def _run_matcher(doc_html: str, base_url: str) -> list[str]:
        page.set_content(
            _inject_base_href(doc_html, base_url),
            wait_until="domcontentloaded",
        )
        result = page.evaluate(
            EXTRACT_JOB_LINKS_JS, [prefix, origin, min_depth, suppress_selector]
        )
        return list(result or [])

    # Top document — always present. declaring fixtures render
    # state 1 from ``pre_filter_urls[0]``, so the top-doc replay URL is
    # ``metadata.top_url`` when set; every single-state fixture omits this
    # key and falls through to ``job_board_url`` byte-identically.
    top_url: str = metadata.get("top_url", job_board_url)
    top_html = (snapshot_dir / "page.html").read_text(encoding="utf-8")
    urls: set[str] = set(_run_matcher(top_html, top_url))

    # Captured frames — present only on schema-v2 fixtures. Each entry is
    # `{file, url}`; the frame doc replays under its own recorded URL so
    # relative anchors inside it resolve against the correct base.
    frames_meta: list[dict[str, str]] = metadata.get("frames", [])
    for entry in frames_meta:
        frame_html = (snapshot_dir / entry["file"]).read_text(encoding="utf-8")
        urls.update(_run_matcher(frame_html, entry["url"]))

    # Captured pagination pages — present only on fixtures
    # captured with ``--paginate``. Same union semantics as frames; the
    # per-state URL matters for boards whose Next control performs a full
    # navigation, and is harmless when successive states share the same
    # URL (SPA pagination).
    pages_meta: list[dict[str, str]] = metadata.get("pages", [])
    for entry in pages_meta:
        page_html = (snapshot_dir / entry["file"]).read_text(encoding="utf-8")
        urls.update(_run_matcher(page_html, entry["url"]))

    # Captured pre-filter-URL states — present only on fixtures
    # whose ``Company`` entry declared non-empty ``pre_filter_urls``.
    # Identical union semantics to ``pages``: each state doc replays
    # under its recorded URL. The runtime path (``DomStrategy`` prefilter
    # branch) unions the same states via successive ``navigate_to`` +
    # ``collect_job_links`` calls, so the harness mirrors it.
    states_meta: list[dict[str, Any]] = metadata.get("states", [])
    # A state entry may carry its own ``frames`` list, replayed under the
    # same union semantics as the top-level ``frames`` key. Present only
    # for boards that combine same-origin frame descent with
    # ``pre_filter_urls`` — Auxis (iCIMS) is the first, where every job
    # anchor lives inside ``#icims_content_iframe`` and the state's top
    # document is an anchorless shell. Absent on every other fixture,
    # which therefore replays byte-identically.
    state_frame_count = 0
    state_page_count = 0
    for state_entry in states_meta:
        state_html = (snapshot_dir / state_entry["file"]).read_text(encoding="utf-8")
        urls.update(_run_matcher(state_html, state_entry["url"]))
        state_frames: list[dict[str, str]] = state_entry.get("frames", [])
        for frame_entry in state_frames:
            state_frame_count += 1
            frame_html = (snapshot_dir / frame_entry["file"]).read_text(
                encoding="utf-8"
            )
            urls.update(_run_matcher(frame_html, frame_entry["url"]))
        # A state entry may likewise carry its own ``pages`` list — the
        # walker's states ≥ 2 *within* that pre-filter state (declaring ×
        # paginate; Accenture is the first). Same union semantics as the
        # top-level ``pages`` key, which holds state 1's pages. Absent on
        # every other fixture, which therefore replays byte-identically.
        state_pages: list[dict[str, str]] = state_entry.get("pages", [])
        for page_entry in state_pages:
            state_page_count += 1
            state_page_html = (snapshot_dir / page_entry["file"]).read_text(
                encoding="utf-8"
            )
            urls.update(_run_matcher(state_page_html, page_entry["url"]))

    actual = len(urls)
    doc_count = (
        1
        + len(frames_meta)
        + len(pages_meta)
        + len(states_meta)
        + state_frame_count
        + state_page_count
    )
    assert actual == expected, (
        f"{metadata['company_name']}: matcher returned {actual} links across "
        f"{doc_count} doc(s) "
        f"(1 top + {len(frames_meta)} frame(s) + {len(pages_meta)} page(s) "
        f"+ {len(states_meta)} state(s) "
        f"+ {state_frame_count} state-frame(s) "
        f"+ {state_page_count} state-page(s)), "
        f"expected {expected} (snapshot: {snapshot_dir.name})"
    )
