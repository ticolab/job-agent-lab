"""Behaviour tests for `EXTRACT_JOB_LINKS_JS` rules that don't need a fixture.

The snapshot regression suite (``test_extractor_snapshots.py``) exercises
the matcher against frozen real-board HTML and asserts counts. Those
fixtures are opaque and site-shaped: they don't cheaply express edge
cases that hinge on a single anchor shape or a specific ``min_depth``
value.

This module fills that gap by driving the matcher against small,
inline-authored HTML documents. Each test constructs a synthetic page
with a known anchor mix, runs the production matcher via
``page.evaluate``, and asserts on the returned URL set. Tests inherit
the browser and JS-disabled context from ``tests/snapshots/conftest.py``
— the JS in the isolated CDP world still runs (``page.evaluate`` uses
its own world), so the matcher itself is unaffected by the page-side
disable.

The file lives under ``tests/snapshots/`` but authors documents inline
via ``page.set_content``; it does not create a fixture directory, so the
snapshot regression harness's discovery (which requires
``page.html``+``metadata.json``) leaves it untouched.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Error, Page

from vacantes.extraction.dom import EXTRACT_JOB_LINKS_JS

_ORIGIN = "https://example.com"
_PREFIX = "/jobs"


def _load(page: Page, body_html: str) -> None:
    """Load a minimal document with the given ``<body>`` contents.

    A ``<base href>`` pointing at ``_ORIGIN`` is injected so relative
    anchors resolve to the same origin the matcher will compare against.
    Without this the ``about:blank`` default base makes every anchor
    cross-origin and the matcher returns an empty set.
    """
    page.set_content(
        f'<!DOCTYPE html><html><head><base href="{_ORIGIN}/"></head>'
        f"<body>{body_html}</body></html>",
        wait_until="domcontentloaded",
    )


def _run(page: Page, args: list[object]) -> set[str]:
    result = page.evaluate(EXTRACT_JOB_LINKS_JS, args)
    return set(result or [])


class TestIdInPathDepthFloor:
    """The ``min_depth`` floor gates the id-in-path bucket only.

    Every case here uses the id-in-path shape (``<prefix>/<segments>``);
    the id-in-query fallback is exercised separately below. The pin is
    that depth = number of path segments after ``<prefix>/``, counted
    after the trailing-slash strip performed inside the matcher.
    """

    def test_default_depth_keeps_shallow_and_deep(self, page: Page) -> None:
        # Baseline: at the default of ``min_depth=1`` both depth-1 and
        # depth-2 anchors survive — byte-identical to the pre-SYS-3
        # matcher. This test pins that identity so any future default
        # change is caught here rather than in the corpus sweep.
        _load(
            page,
            '<a href="/jobs/a">a</a><a href="/jobs/a/b">ab</a>',
        )
        urls = _run(page, [_PREFIX, _ORIGIN, 1])
        assert urls == {
            f"{_ORIGIN}/jobs/a",
            f"{_ORIGIN}/jobs/a/b",
        }

    def test_depth_two_keeps_deep_drops_shallow(self, page: Page) -> None:
        # The C9 fix in miniature: raising the floor to 2 drops the
        # depth-1 anchor while leaving the depth-2 anchor intact.
        _load(
            page,
            '<a href="/jobs/a">a</a><a href="/jobs/a/b">ab</a>',
        )
        urls = _run(page, [_PREFIX, _ORIGIN, 2])
        assert urls == {f"{_ORIGIN}/jobs/a/b"}

    def test_trailing_slash_counts_as_shallow(self, page: Page) -> None:
        # ``/jobs/a/`` and ``/jobs/a`` both count as depth 1 because the
        # matcher strips the trailing slash before splitting. Both are
        # dropped when ``min_depth=2``.
        _load(
            page,
            '<a href="/jobs/a/">a-trailing</a><a href="/jobs/a/b">ab</a>',
        )
        urls = _run(page, [_PREFIX, _ORIGIN, 2])
        assert urls == {f"{_ORIGIN}/jobs/a/b"}


class TestFallbackAndQueryBranch:
    """The path/query fallback and the query branch's depth-immunity.

    The matcher prefers the id-in-path bucket over the id-in-query bucket
    (Lever fix), falling back to the query bucket only when the path
    bucket is empty. A depth-emptied path bucket is indistinguishable
    from a naturally empty one — the fallback fires the same way.
    """

    def test_depth_drained_path_bucket_falls_back_to_query(self, page: Page) -> None:
        # Path anchors are all at depth 1 and get dropped by
        # ``min_depth=2``; the query-shape anchor on the prefix itself is
        # the only surviving match. Mirrors what a C9 board would look
        # like if it also carried a Lever-style facet URL.
        _load(
            page,
            '<a href="/jobs/a">a</a>'
            '<a href="/jobs/b">b</a>'
            '<a href="/jobs?req=1">facet</a>',
        )
        urls = _run(page, [_PREFIX, _ORIGIN, 2])
        assert urls == {f"{_ORIGIN}/jobs?req=1"}

    def test_query_branch_immune_to_min_depth(self, page: Page) -> None:
        # ``min_depth`` gates only the id-in-path branch. With no path
        # anchors at all, the query-shape anchor is returned regardless
        # of the floor — even at ``min_depth=3`` which would drop every
        # single-tail anchor if it were consulted here.
        _load(page, '<a href="/jobs?req=42">only-query</a>')
        urls = _run(page, [_PREFIX, _ORIGIN, 3])
        assert urls == {f"{_ORIGIN}/jobs?req=42"}


class TestBackwardCompatDefault:
    """Callers that don't thread ``min_depth`` keep the pre-SYS-3 behaviour."""

    def test_two_element_args_list_defaults_to_depth_one(self, page: Page) -> None:
        # Any external caller still passing ``[basePath, careerOrigin]``
        # (older skill-copy revisions, scratch scripts) must see the same
        # anchors it always did. The JS defaults ``minDepth`` to 1 when
        # the third slot is omitted; this pins that contract.
        _load(
            page,
            '<a href="/jobs/a">a</a><a href="/jobs/a/b">ab</a>',
        )
        # Deliberately a 2-element list — no third arg.
        urls = _run(page, [_PREFIX, _ORIGIN])
        assert urls == {
            f"{_ORIGIN}/jobs/a",
            f"{_ORIGIN}/jobs/a/b",
        }

    def test_three_element_args_list_defaults_suppression_off(self, page: Page) -> None:
        # SYS-14 adds a fourth slot. Every caller that still passes three
        # elements (the probe, ``verify_urlset_diff.py``, older skill-copy
        # revisions) must see suppression disabled — the JS defaults
        # ``suppressSelector`` to null and the gate is skipped entirely.
        # This is the omitted-arg contract the corpus sweep depends on.
        _load(
            page,
            '<div data-automation="featured-opportunities">'
            '<a href="/jobs/featured">f</a>'
            "</div>"
            '<a href="/jobs/real">r</a>',
        )
        urls = _run(page, [_PREFIX, _ORIGIN, 1])
        assert urls == {
            f"{_ORIGIN}/jobs/featured",
            f"{_ORIGIN}/jobs/real",
        }


class TestSuppressAncestorSelector:
    """SYS-14: ``anchor.closest(selector)`` drops container-scoped anchors.

    The C16 shape (Ulteig): a "Featured opportunities" section whose
    recommendation anchors share the origin, prefix, depth, and
    id-in-query URL shape of the real filtered results, so no URL-layer
    discriminator exists. UKG marks the wrapper with a platform-owned
    ``data-automation`` attribute; the markup below mirrors the P4
    capture at ``spike/evidence/ulteig_featured_section.html``.
    """

    _SUPPRESS = '[data-automation="featured-opportunities"]'

    def test_anchor_inside_container_dropped_sibling_kept(self, page: Page) -> None:
        # The core contract, in the Ulteig shape: two anchors that are
        # indistinguishable at the URL layer, separated only by their
        # ancestor container.
        _load(
            page,
            '<div data-automation="featured-opportunities">'
            '<a href="/jobs/featured-1">f1</a>'
            '<a href="/jobs/featured-2">f2</a>'
            "</div>"
            '<a href="/jobs/real-1">r1</a>',
        )
        urls = _run(page, [_PREFIX, _ORIGIN, 1, self._SUPPRESS])
        assert urls == {f"{_ORIGIN}/jobs/real-1"}

    def test_deeply_nested_anchor_dropped_by_outer_container(self, page: Page) -> None:
        # ``closest()`` walks the full ancestor chain, so the selector
        # need only match some ancestor — not the anchor's parent. UKG
        # nests its recommendation anchors inside per-row ``<tr>``
        # elements several levels below the marked wrapper.
        _load(
            page,
            '<div data-automation="featured-opportunities">'
            "<table><tbody>"
            '<tr data-automation="featured-opportunity"><td><span>'
            '<a href="/jobs/featured-deep">deep</a>'
            "</span></td></tr>"
            "</tbody></table>"
            "</div>"
            '<a href="/jobs/real-1">r1</a>',
        )
        urls = _run(page, [_PREFIX, _ORIGIN, 1, self._SUPPRESS])
        assert urls == {f"{_ORIGIN}/jobs/real-1"}

    def test_non_matching_selector_is_inert(self, page: Page) -> None:
        # A selector that matches nothing on the page leaves the result
        # set identical to a default run — suppression never invents
        # drops.
        body = (
            '<div data-automation="featured-opportunities">'
            '<a href="/jobs/featured">f</a>'
            "</div>"
            '<a href="/jobs/real">r</a>'
        )
        _load(page, body)
        with_selector = _run(page, [_PREFIX, _ORIGIN, 1, ".no-such-container"])
        _load(page, body)
        without_selector = _run(page, [_PREFIX, _ORIGIN, 1])
        assert with_selector == without_selector

    def test_visible_anchor_inside_container_is_dropped(self, page: Page) -> None:
        # Suppression is orthogonal to visibility — that is the point of
        # the class. The featured section is fully rendered and its
        # anchors pass ``checkVisibility``; they are excluded because of
        # *where* they live, not because they are hidden. An explicit
        # ``display:block`` guards against a future refactor that folds
        # suppression into the visibility gate.
        _load(
            page,
            '<div data-automation="featured-opportunities" '
            'style="display:block">'
            '<a href="/jobs/featured" style="display:block">f</a>'
            "</div>"
            '<a href="/jobs/real">r</a>',
        )
        urls = _run(page, [_PREFIX, _ORIGIN, 1, self._SUPPRESS])
        assert urls == {f"{_ORIGIN}/jobs/real"}

    def test_suppression_drained_path_bucket_falls_back_to_query(
        self, page: Page
    ) -> None:
        # Gate placement (before bucketing) makes this fall out for
        # free: when suppression empties the id-in-path bucket, the
        # matcher takes the id-in-query fallback exactly as it does for
        # a naturally empty or ``min_depth``-drained bucket. Pinning it
        # here guards the placement itself — moving the gate after
        # bucketing would silently return an empty set instead.
        _load(
            page,
            '<div data-automation="featured-opportunities">'
            '<a href="/jobs/featured">f</a>'
            "</div>"
            '<a href="/jobs?req=42">q</a>',
        )
        urls = _run(page, [_PREFIX, _ORIGIN, 1, self._SUPPRESS])
        assert urls == {f"{_ORIGIN}/jobs?req=42"}

    def test_suppression_composes_with_min_depth(self, page: Page) -> None:
        # Both gates active and independent: ``min_depth=2`` drops the
        # shallow anchor, suppression drops the container anchor, and the
        # deep non-suppressed anchor survives both.
        _load(
            page,
            '<div data-automation="featured-opportunities">'
            '<a href="/jobs/featured/deep">fd</a>'
            "</div>"
            '<a href="/jobs/shallow">s</a>'
            '<a href="/jobs/real/deep">rd</a>',
        )
        urls = _run(page, [_PREFIX, _ORIGIN, 2, self._SUPPRESS])
        assert urls == {f"{_ORIGIN}/jobs/real/deep"}

    def test_shadow_root_anchor_not_suppressed_by_light_dom_host_container(
        self, page: Page
    ) -> None:
        # Boundary semantics, per ARCHITECTURE_PROPOSAL_R2.md §4.7:
        # ``closest()`` does not cross shadow boundaries, matching the
        # matcher's per-document scan model — each scanned document
        # applies the gate independently. So an anchor inside an open
        # shadow root is NOT suppressed by a container that wraps its
        # host in the light DOM. This is intended behaviour, not an
        # oversight: a future "fix" that walks through the host would
        # break the per-document model and is caught here.
        _load(
            page,
            '<div data-automation="featured-opportunities">'
            '<div id="host"></div></div>'
            '<a href="/jobs/real">r</a>',
        )
        page.evaluate(
            """() => {
                const host = document.getElementById('host');
                const root = host.attachShadow({ mode: 'open' });
                root.innerHTML = '<a href="/jobs/in-shadow">s</a>';
            }"""
        )
        urls = _run(page, [_PREFIX, _ORIGIN, 1, self._SUPPRESS])
        assert urls == {
            f"{_ORIGIN}/jobs/real",
            f"{_ORIGIN}/jobs/in-shadow",
        }

    def test_shadow_root_anchor_suppressed_by_container_inside_same_root(
        self, page: Page
    ) -> None:
        # The converse of the boundary test: within a single scanned
        # document (here, the shadow root itself) the gate applies
        # normally. Together the two tests pin "per-document, and only
        # per-document".
        _load(page, '<div id="host"></div><a href="/jobs/real">r</a>')
        page.evaluate(
            """() => {
                const host = document.getElementById('host');
                const root = host.attachShadow({ mode: 'open' });
                root.innerHTML =
                    '<div data-automation="featured-opportunities">' +
                    '<a href="/jobs/shadow-featured">sf</a></div>' +
                    '<a href="/jobs/shadow-real">sr</a>';
            }"""
        )
        urls = _run(page, [_PREFIX, _ORIGIN, 1, self._SUPPRESS])
        assert urls == {
            f"{_ORIGIN}/jobs/real",
            f"{_ORIGIN}/jobs/shadow-real",
        }


class TestSuppressSelectorValidation:
    """An invalid suppression selector is a loud error, never a no-op.

    ``closest()`` throws SyntaxError on an invalid selector, but only
    when it executes. The matcher therefore validates the selector once
    up front, so the error surfaces on *every* document rather than only
    on those that happen to have a surviving anchor.

    Note on choosing test inputs: ``"[unclosed"`` is **not** a useful
    invalid-selector example — per the CSS Syntax spec an unclosed block
    is auto-closed at EOF, so Chromium parses it as the valid
    attribute-presence selector ``[unclosed]`` and nothing throws. Use a
    genuinely unparseable selector (``div:::bad``, ``a[``, ``>>>``, the
    empty string) instead. This tripped up the original SYS-14 test
    authoring; the note exists so the guard is not mistakenly declared
    broken next time.
    """

    def test_invalid_selector_raises(self, page: Page) -> None:
        _load(page, '<a href="/jobs/real">r</a>')
        with pytest.raises(Error, match="not a valid CSS selector"):
            _run(page, [_PREFIX, _ORIGIN, 1, "div:::bad"])

    def test_invalid_selector_raises_on_anchorless_document(self, page: Page) -> None:
        # The upfront-validation pin. Without the probe, this document
        # would never reach ``closest()`` and a typo'd selector would
        # silently behave like "no suppression" — the exact silent-config
        # -rot failure the guard exists to prevent.
        _load(page, "<p>no anchors here</p>")
        with pytest.raises(Error, match="not a valid CSS selector"):
            _run(page, [_PREFIX, _ORIGIN, 1, "div:::bad"])

    def test_empty_selector_raises(self, page: Page) -> None:
        # The empty string is a plausible catalog typo
        # (``suppress_ancestor_selector=""``) and is rejected by the CSS
        # parser, so it surfaces as a loud error rather than reading as
        # "suppression off". Only ``None`` disables the gate.
        _load(page, '<a href="/jobs/real">r</a>')
        with pytest.raises(Error, match="not a valid CSS selector"):
            _run(page, [_PREFIX, _ORIGIN, 1, ""])

    def test_unclosed_bracket_is_valid_css_and_does_not_raise(self, page: Page) -> None:
        # Documents the CSS Syntax auto-close rule described in the class
        # docstring: ``[data-x`` parses as ``[data-x]`` (attribute
        # presence), so it suppresses matching anchors rather than
        # erroring. Pinned so the guard's scope stays honest — it catches
        # unparseable selectors, not every selector a human would call a
        # typo.
        _load(
            page,
            '<div data-automation="featured-opportunities">'
            '<a href="/jobs/featured">f</a>'
            "</div>"
            '<a href="/jobs/real">r</a>',
        )
        urls = _run(page, [_PREFIX, _ORIGIN, 1, "[data-automation"])
        assert urls == {f"{_ORIGIN}/jobs/real"}

    def test_error_message_names_the_offending_selector(self, page: Page) -> None:
        _load(page, '<a href="/jobs/real">r</a>')
        with pytest.raises(Error, match=r"div:::bad"):
            _run(page, [_PREFIX, _ORIGIN, 1, "div:::bad"])
