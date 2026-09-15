"""JS↔Python parity test for the URL-layer matcher semantics.

The Talentbrew adapter (SYS-17) filters absolutized hrefs through
:func:`~vacantes.extraction.ats.talentbrew.apply_link_rule`, a
Python mirror of the URL-layer half of
``extraction/dom/assets/collect_links.js``. Two implementations of the
same semantics is a maintenance hazard, so this test executes the
*real* JS asset against a synthetic anchor document in a Chromium
page and asserts set-equality with ``apply_link_rule`` over the same
absolutized href list. Any future URL-layer change to
``collect_links.js`` that this Python mirror fails to track fails a
case here mechanically, without depending on anyone remembering a
convention.

The cases below cover the URL-layer axes the mirror docstring
enumerates: same-origin gate, id-in-path bucket at multiple depths,
``min_depth`` floor (including the path-bucket-drained-to-query
fallback), id-in-query bucket, path-bucket preference when both
buckets are populated, trailing-slash strip, and fragment collapse.
The DOM-layer axes the mirror deliberately does NOT cover (visibility,
container suppression, shadow/frame walking) are pinned in
``tests/snapshots/test_matcher_rules.py`` and are structurally out of
scope here — the parity claim is URL-layer only, matching the
mirror's docstring.
"""

from __future__ import annotations

from urllib.parse import urljoin

from playwright.sync_api import Page

from tests.snapshots.test_extractor_snapshots import _inject_base_href
from vacantes.extraction.ats.talentbrew import (
    apply_link_rule,
    parse_anchor_hrefs,
)
from vacantes.extraction.dom import EXTRACT_JOB_LINKS_JS

_ORIGIN = "https://example.test"
_BASE_URL = _ORIGIN + "/careers"


def _run_parity(
    page: Page,
    fragment: str,
    *,
    base_path: str,
    min_depth: int = 1,
) -> set[str]:
    """Run JS and Python matchers on ``fragment``, assert parity, return the set.

    The JS side runs the real ``EXTRACT_JOB_LINKS_JS`` asset against a
    Chromium page whose body is ``fragment`` and whose ``<base href>``
    points at ``_BASE_URL`` (so relative hrefs resolve to ``_ORIGIN``).
    The Python side replicates the Talentbrew adapter's pre-absolutize
    step (``urljoin(base_url, href)`` mirrors the browser's own
    ``a.href`` resolution against ``<base>``) and hands the same URL
    list to :func:`apply_link_rule`. Suppression is passed as ``None``
    on the JS side to match the mirror's no-suppression stance.
    """
    html = f"<html><head></head><body>{fragment}</body></html>"
    page.set_content(
        _inject_base_href(html, _BASE_URL),
        wait_until="domcontentloaded",
    )
    js_urls = page.evaluate(EXTRACT_JOB_LINKS_JS, [base_path, _ORIGIN, min_depth, None])
    js_set: set[str] = set(js_urls or [])

    raw_hrefs = parse_anchor_hrefs(fragment)
    abs_hrefs = [urljoin(_BASE_URL, href) for href in raw_hrefs]
    py_set = set(
        apply_link_rule(
            abs_hrefs, origin=_ORIGIN, base_path=base_path, min_depth=min_depth
        )
    )
    assert js_set == py_set, (
        "JS/Python matcher divergence:\n"
        f"  JS only: {sorted(js_set - py_set)}\n"
        f"  Py only: {sorted(py_set - js_set)}"
    )
    return js_set


def test_parity_id_in_path_mixed_depths(page: Page) -> None:
    """Depth-1 and deeper anchors both land under the path bucket at min_depth=1."""
    fragment = """
        <a href="/jobs/123">shallow</a>
        <a href="/jobs/eng/456">deep</a>
        <a href="/jobs/eng/senior/789">deeper</a>
    """
    got = _run_parity(page, fragment, base_path="/jobs", min_depth=1)
    assert got == {
        f"{_ORIGIN}/jobs/123",
        f"{_ORIGIN}/jobs/eng/456",
        f"{_ORIGIN}/jobs/eng/senior/789",
    }


def test_parity_min_depth_two_drops_shallow(page: Page) -> None:
    """min_depth=2 keeps depth-2+ anchors and drops depth-1 chrome."""
    fragment = """
        <a href="/jobs/culture">chrome</a>
        <a href="/jobs/eng/456">real</a>
        <a href="/jobs/eng/senior/789">deeper</a>
    """
    got = _run_parity(page, fragment, base_path="/jobs", min_depth=2)
    assert got == {
        f"{_ORIGIN}/jobs/eng/456",
        f"{_ORIGIN}/jobs/eng/senior/789",
    }


def test_parity_trailing_slash_is_stripped_for_bucketing(page: Page) -> None:
    """A single trailing ``/`` is stripped for the depth check only.

    The emitted URL retains the original trailing slash.
    """
    fragment = """
        <a href="/jobs/123">no-slash</a>
        <a href="/jobs/456/">with-slash</a>
    """
    got = _run_parity(page, fragment, base_path="/jobs", min_depth=1)
    # Emitted URLs preserve the original trailing slash — the strip is
    # bucketing-only. Both anchors count as depth 1.
    assert got == {
        f"{_ORIGIN}/jobs/123",
        f"{_ORIGIN}/jobs/456/",
    }


def test_parity_fragment_collapse_dedups(page: Page) -> None:
    """/jobs/123 and /jobs/123#apply collapse to one fragment-stripped entry."""
    fragment = """
        <a href="/jobs/123">no-frag</a>
        <a href="/jobs/123#apply">with-frag</a>
        <a href="/jobs/123#other">other-frag</a>
    """
    got = _run_parity(page, fragment, base_path="/jobs", min_depth=1)
    assert got == {f"{_ORIGIN}/jobs/123"}


def test_parity_id_in_query_bucket(page: Page) -> None:
    """Path == base_path with non-empty query lands in the query bucket."""
    fragment = """
        <a href="/careers?pId=180">q1</a>
        <a href="/careers?pId=181">q2</a>
        <a href="/careers">no-query, dropped by both buckets</a>
    """
    got = _run_parity(page, fragment, base_path="/careers", min_depth=1)
    assert got == {
        f"{_ORIGIN}/careers?pId=180",
        f"{_ORIGIN}/careers?pId=181",
    }


def test_parity_path_bucket_preferred_when_both_populated(page: Page) -> None:
    """Path-bucket-wins: query anchors dropped when path bucket is non-empty."""
    fragment = """
        <a href="/careers/123-eng">path</a>
        <a href="/careers?pId=180">query, dropped by preference</a>
    """
    got = _run_parity(page, fragment, base_path="/careers", min_depth=1)
    assert got == {f"{_ORIGIN}/careers/123-eng"}


def test_parity_min_depth_drained_path_falls_back_to_query(page: Page) -> None:
    """A path bucket fully drained by min_depth falls back to the query bucket."""
    fragment = """
        <a href="/careers/chrome">depth 1, drained by min_depth=2</a>
        <a href="/careers?pId=180">query, promoted by fallback</a>
    """
    got = _run_parity(page, fragment, base_path="/careers", min_depth=2)
    assert got == {f"{_ORIGIN}/careers?pId=180"}


def test_parity_cross_origin_dropped(page: Page) -> None:
    """Anchors whose origin differs from ``origin`` are dropped."""
    fragment = """
        <a href="/jobs/123">same-origin</a>
        <a href="https://other.test/jobs/999">cross-origin, dropped</a>
        <a href="https://apply.example.test/jobs/888">sub-origin, dropped</a>
    """
    got = _run_parity(page, fragment, base_path="/jobs", min_depth=1)
    assert got == {f"{_ORIGIN}/jobs/123"}


def test_parity_empty_document(page: Page) -> None:
    """A document with no anchors yields the empty set on both sides."""
    got = _run_parity(page, "<div>no jobs here</div>", base_path="/jobs")
    assert got == set()


def test_parity_unparseable_and_scheme_only_anchors_dropped(page: Page) -> None:
    """mailto:/javascript: anchors and href-less anchors are dropped by both."""
    fragment = """
        <a>no href</a>
        <a href="">empty href</a>
        <a href="mailto:jobs@example.test">mailto</a>
        <a href="javascript:void(0)">js</a>
        <a href="/jobs/123">real</a>
    """
    got = _run_parity(page, fragment, base_path="/jobs", min_depth=1)
    assert got == {f"{_ORIGIN}/jobs/123"}
