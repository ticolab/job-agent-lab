"""Discovery-pass unit tests for ``FIND_NEXT_CONTROL_JS``.

The pagination walker in ``collector.py`` composes two independent
concerns: *discovery* (locate the next-page affordance and stamp it with
the ``data-jal-next`` marker) and *loop mechanics* (click, settle,
re-collect, terminate). This module isolates the first half — synthetic
DOM fragments authored inline via ``page.set_content``, the discovery JS
invoked with ``dryRun=True`` so no click ever fires, and the returned
descriptor asserted against.

Tests inherit the JS-disabled browser context from
``tests/snapshots/conftest.py`` — discovery is pure DOM inspection under
``page.evaluate`` (which runs in the isolated CDP world regardless of the
page-side JS-disable), so an intentionally-inert document is the right
substrate. Cases live under ``tests/snapshots/`` rather than
``tests/unit/`` because they need a real browser to exercise
``checkVisibility``, ``getComputedStyle`` and ``element.hidden``.

The full walker loop (click + settle + termination) is covered separately
in ``test_pagination_walker.py``, which uses a JS-enabled context and
real file:// navigation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from playwright.sync_api import Page

from job_agent_lab.extraction.dom import FIND_NEXT_CONTROL_JS

# Discovery does not care about origin — only DOM shape — but a stable
# base is still injected so any relative anchors resolve rather than
# throw ``Invalid URL`` inside the visibility gate.
_BASE = "https://example.com/"


def _load(page: Page, body_html: str) -> None:
    """Load a minimal document with the given ``<body>`` contents.

    The base href points at ``_BASE`` so ``<a href="/foo">`` resolves.
    Discovery ignores href content, but a resolved anchor keeps the
    document well-formed for other DOM operations.
    """
    page.set_content(
        f'<!DOCTYPE html><html><head><base href="{_BASE}"></head>'
        f"<body>{body_html}</body></html>",
        wait_until="domcontentloaded",
    )


def _discover(page: Page, *, dry_run: bool = True) -> dict[str, Any]:
    """Run the discovery JS and return its descriptor dict."""
    # ``page.evaluate`` is typed ``Any``; the discovery JS's return shape
    # is a documented ``{found, signal, text}`` dict, so we narrow here.
    return cast(dict[str, Any], page.evaluate(FIND_NEXT_CONTROL_JS, [dry_run]))


def _marker_count(page: Page) -> int:
    """Count elements currently carrying the ``data-jal-next`` marker."""
    count = page.evaluate("() => document.querySelectorAll('[data-jal-next]').length")
    return int(count)


class TestSignalPositive:
    """One test per signal — the minimal DOM that should fire it.

    Each case is intentionally starved of any higher-priority signal so
    the tested rule is the only one that can win.
    """

    def test_rel_next_fires(self, page: Page) -> None:
        # a[rel~="next"] is signal 1 and beats every subsequent rule.
        _load(page, '<a rel="next" href="/p2">go</a>')
        result = _discover(page)
        assert result == {"found": True, "signal": "rel-next", "text": "go"}

    def test_aria_label_next_fires(self, page: Page) -> None:
        # BCG's actual shape: aria-label carries the intent, the visible
        # text is a chevron / icon that ``textContent`` cannot match on.
        _load(page, '<button aria-label="View next page">›</button>')
        result = _discover(page)
        assert result == {
            "found": True,
            "signal": "aria-label",
            "text": "View next page",
        }

    def test_text_next_fires(self, page: Page) -> None:
        # Techwarely's actual shape: a bare ``<a>Next</a>`` with no
        # rel / aria-label. Case-insensitive so "NEXT" / "next" both hit.
        _load(page, '<a href="/p2">Next</a>')
        result = _discover(page)
        assert result == {"found": True, "signal": "text-next", "text": "Next"}

    def test_numeric_successor_fires(self, page: Page) -> None:
        # ``<span>1</span><a>2</a>`` inside a common parent: signal 4
        # locates the pure-integer indicator ``1`` and pairs it with
        # the sibling anchor whose text is exactly ``2``.
        _load(
            page,
            '<nav><span>1</span><a href="/p2">2</a><a href="/p3">3</a></nav>',
        )
        result = _discover(page)
        assert result == {
            "found": True,
            "signal": "numeric-successor",
            "text": "2",
        }


class TestBoardShapes:
    """DOM shapes lifted from the two boards that motivated SYS-5."""

    def test_bcg_shape_next_wins_over_previous(self, page: Page) -> None:
        # BCG renders both pagers with aria-label; only the "next" one
        # must match. "View previous page" does not contain the
        # substring "next" and is correctly ignored.
        _load(
            page,
            "".join(
                [
                    '<button aria-label="View previous page">‹</button>',
                    '<button aria-label="View next page">›</button>',
                    '<button aria-label="Page 2">2</button>',
                ]
            ),
        )
        result = _discover(page)
        assert result["found"] is True
        assert result["signal"] == "aria-label"
        assert result["text"] == "View next page"

    def test_techwarely_shape_text_next(self, page: Page) -> None:
        # Techwarely, page 1: Prev is de-anchored (span, not a candidate),
        # "1" is the current-page indicator (span), "2" is an anchor.
        # The bare "Next" anchor is the winner — text-next has priority
        # over the numeric successor even though both are present.
        _load(
            page,
            "".join(
                [
                    '<div class="pager">',
                    "<span>Prev</span>",
                    "<span>1</span>",
                    '<a href="?page=2">2</a>',
                    '<a href="?page=2">Next</a>',
                    "</div>",
                ]
            ),
        )
        result = _discover(page)
        assert result["found"] is True
        assert result["signal"] == "text-next"
        assert result["text"] == "Next"


class TestExclusions:
    """Elements the walker must never treat as candidates."""

    def test_aria_disabled_is_skipped(self, page: Page) -> None:
        # A Next control on the last page usually keeps its DOM but
        # flips ``aria-disabled="true"``. Discovery must not treat it
        # as a candidate; without a live control the result is a
        # not-found termination signal for the walker.
        _load(page, '<button aria-disabled="true">Next</button>')
        result = _discover(page)
        assert result == {"found": False, "signal": None, "text": None}

    def test_disabled_attribute_is_skipped(self, page: Page) -> None:
        _load(page, "<button disabled>Next</button>")
        result = _discover(page)
        assert result == {"found": False, "signal": None, "text": None}

    def test_hidden_control_is_skipped(self, page: Page) -> None:
        # ``display: none`` fails ``checkVisibility({checkVisibilityCSS: true})``
        # — the same gate the matcher applies to anchors.
        _load(page, '<button style="display: none">Next</button>')
        result = _discover(page)
        assert result == {"found": False, "signal": None, "text": None}

    def test_span_only_pager_yields_nothing(self, page: Page) -> None:
        # Techwarely on its last page: every pagination element is a
        # de-anchored span. No <a>/<button> means no candidate for any
        # signal — the walker must terminate cleanly.
        _load(
            page,
            "".join(
                [
                    '<div class="pager">',
                    "<span>Prev</span>",
                    "<span>1</span>",
                    "<span>2</span>",
                    "<span>Next</span>",
                    "</div>",
                ]
            ),
        )
        result = _discover(page)
        assert result == {"found": False, "signal": None, "text": None}


class TestPriorityOrder:
    """When more than one signal fires, the earlier rule wins."""

    def test_rel_next_beats_text_next(self, page: Page) -> None:
        # Both a ``<a rel="next">`` and a bare ``<a>Next</a>`` are
        # candidates; the rel-next rule is scanned first and takes it.
        _load(
            page,
            "".join(
                [
                    '<a rel="next" href="/p2">go</a>',
                    '<a href="/p2">Next</a>',
                ]
            ),
        )
        result = _discover(page)
        assert result["signal"] == "rel-next"
        assert result["text"] == "go"

    def test_aria_label_beats_text_next(self, page: Page) -> None:
        _load(
            page,
            "".join(
                [
                    '<button aria-label="Go to next">›</button>',
                    '<a href="/p2">Next</a>',
                ]
            ),
        )
        result = _discover(page)
        assert result["signal"] == "aria-label"
        assert result["text"] == "Go to next"

    def test_text_next_beats_numeric_successor(self, page: Page) -> None:
        # Techwarely's real shape: both would fire but the priority
        # order pins the text-next winner, so the walker's marker sits
        # on the "Next" anchor rather than the numeric "2" anchor.
        _load(
            page,
            "".join(
                [
                    "<nav>",
                    "<span>1</span>",
                    '<a href="?p=2">2</a>',
                    '<a href="?p=2">Next</a>',
                    "</nav>",
                ]
            ),
        )
        result = _discover(page)
        assert result["signal"] == "text-next"


class TestMarkerHygiene:
    """The ``data-jal-next`` marker must stay a single-winner attribute."""

    def test_dry_run_never_stamps(self, page: Page) -> None:
        # Every other test in this module runs with ``dryRun=True``,
        # so this is the only place we assert that contract directly.
        _load(page, '<a href="/p2">Next</a>')
        result = _discover(page, dry_run=True)
        assert result["found"] is True
        assert _marker_count(page) == 0

    def test_non_dry_run_stamps_exactly_one_element(self, page: Page) -> None:
        _load(
            page,
            "".join(
                [
                    '<a rel="next" href="/p2">go</a>',
                    '<a href="/p2">Next</a>',
                ]
            ),
        )
        _discover(page, dry_run=False)
        assert _marker_count(page) == 1

    def test_marker_cleared_and_restamped_across_passes(self, page: Page) -> None:
        # Pass 1 stamps the rel=next anchor. We then manually place a
        # stale marker on a sibling to simulate a leftover from a prior
        # state. Pass 2 must clear that stale marker (and re-stamp the
        # real winner), leaving exactly one marker on the same original
        # winner. This guards against ``page.click('[data-jal-next]')``
        # accidentally targeting a stale element.
        _load(
            page,
            "".join(
                [
                    '<a rel="next" id="winner" href="/p2">go</a>',
                    '<a id="stale" href="/p2">Next</a>',
                ]
            ),
        )
        _discover(page, dry_run=False)
        # Stamp a stale marker on the runner-up to mimic drift.
        page.evaluate(
            "() => document.getElementById('stale').setAttribute('data-jal-next', '')"
        )
        assert _marker_count(page) == 2
        _discover(page, dry_run=False)
        assert _marker_count(page) == 1
        # And the marker is on the correct winner, not the stale sibling.
        winner_id = page.evaluate("() => document.querySelector('[data-jal-next]').id")
        assert winner_id == "winner"

    def test_not_found_still_clears_stale_marker(self, page: Page) -> None:
        # Even when discovery yields no winner (last-page case), any
        # marker inherited from an earlier state must be cleared so
        # the driver never clicks a phantom control.
        _load(page, '<a id="stale" href="/p2">Continue</a>')
        page.evaluate(
            "() => document.getElementById('stale').setAttribute('data-jal-next', '')"
        )
        assert _marker_count(page) == 1
        result = _discover(page, dry_run=False)
        assert result["found"] is False
        assert _marker_count(page) == 0


# ---------------------------------------------------------------------------
# SYS-11 additions: signals 5 (class-next) and 6 (load-more) plus the shared
# next-allowlist tightening of signals 2 and 3.
# ---------------------------------------------------------------------------


class TestSignalClassNext:
    """Signal 5: ``a[class~="next"], button[class~="next"]``.

    Motivating shape from the R2 ledger's Movate entry: an icon-only SJB
    pager anchor with an empty trimmed text, no ``rel``, no ``aria-label``,
    and the current-page indicator in a sibling ``<li>`` outside signal 4's
    same-parent scope. Signal 5 exists to catch this class of markup and
    is deliberately placed below every semantic signal so its class-name
    promiscuity (carousels / wizards) is bounded.
    """

    def test_movate_sjb_shape_class_next_wins(self, page: Page) -> None:
        # Verbatim from the ledger's Movate entry — an icon-only anchor
        # with ``class="next page-numbers"`` inside a sibling ``<li>``.
        # None of signals 1-4 can fire on this markup: no ``rel``, no
        # ``aria-label``, empty trimmed text (icon child), and the ``1``
        # current-page indicator lives in a *sibling* ``<li>`` — signal
        # 4 scopes the successor search to the indicator's parent
        # ``<li>``, not up to the shared ``<ul>``.
        _load(
            page,
            "".join(
                [
                    '<nav aria-label="Page navigation">',
                    '<ul class="pagination">',
                    '<li class="list-item">',
                    '<span class="page-numbers current">1</span>',
                    "</li>",
                    '<li class="list-item">',
                    '<a class="page-numbers" '
                    'href="?paged=2&selected_location=Costa+Rica">2</a>',
                    "</li>",
                    '<li class="list-item">',
                    '<a class="next page-numbers" '
                    'href="?paged=2&selected_location=Costa+Rica">'
                    '<i class="fa fa-angle-right"></i>'
                    "</a>",
                    "</li>",
                    "</ul></nav>",
                ]
            ),
        )
        result = _discover(page)
        assert result["found"] is True
        assert result["signal"] == "class-next"

    def test_class_next_uses_token_match_not_substring(self, page: Page) -> None:
        # ``class="anextbutton"`` contains the substring "next" but the
        # ``[class~="next"]`` token operator (``classList.contains('next')``)
        # correctly requires ``next`` to be a standalone whitespace-
        # separated token. This case starves the classic four signals too,
        # so the only possible winner would be signal 5 — and it must not
        # fire.
        _load(page, '<a class="anextbutton" href="/p2">›</a>')
        result = _discover(page)
        assert result == {"found": False, "signal": None, "text": None}

    def test_class_next_button_variant(self, page: Page) -> None:
        # Symmetry with the anchor case: ``<button class="next">`` must
        # win when nothing above it fires.
        _load(page, '<button class="next">›</button>')
        result = _discover(page)
        assert result["found"] is True
        assert result["signal"] == "class-next"


class TestSignalLoadMore:
    """Signal 6: prefix-anchored ``/^(load|show)\\s*more/i`` on text or aria-label.

    Motivating shape from the R2 ledger's Svitla entry: a persistent
    ``Load More`` button paired with two marketing carousels whose
    ``aria-label="Next office"`` / ``"Next review"`` used to false-positive
    on the pre-SYS-11 signal 2. The allowlist tightening now rejects those
    labels, so the Load-More button wins as intended.
    """

    def test_svitla_shape_load_more_wins_over_carousels(self, page: Page) -> None:
        # Verbatim shape from the ledger: listing Load-More button plus
        # both carousel pairs (previous/next office and previous/next
        # review). Under the tightened signal 2, ``Next office`` /
        # ``Next review`` do not match the allowlist (``office`` /
        # ``review`` are not in the allowed-token set), so no signal
        # above 6 can fire and the Load-More button wins.
        _load(
            page,
            "".join(
                [
                    '<button aria-label="Previous office">‹</button>',
                    '<button aria-label="Next office">›</button>',
                    '<button aria-label="Previous review">‹</button>',
                    '<button aria-label="Next review">›</button>',
                    "<button>Load More</button>",
                ]
            ),
        )
        result = _discover(page)
        assert result == {
            "found": True,
            "signal": "load-more",
            "text": "Load More",
        }

    def test_load_more_case_insensitive(self, page: Page) -> None:
        _load(page, "<button>load more</button>")
        result = _discover(page)
        assert result["signal"] == "load-more"

    def test_show_more_variant(self, page: Page) -> None:
        # ``Show more`` is the second half of the ``(load|show)`` group.
        _load(page, "<button>Show more</button>")
        result = _discover(page)
        assert result["signal"] == "load-more"

    def test_load_more_jobs_suffix_allowed(self, page: Page) -> None:
        # The regex is prefix-anchored, not fully anchored, so
        # ``Load More Jobs`` and ``Load more openings`` both match.
        _load(page, "<button>Load More Jobs</button>")
        result = _discover(page)
        assert result["signal"] == "load-more"
        assert result["text"] == "Load More Jobs"

    def test_load_more_via_aria_label(self, page: Page) -> None:
        # An icon-only Load-More button — text is empty but the
        # aria-label carries the phrase.
        _load(page, '<button aria-label="Load more results">＋</button>')
        result = _discover(page)
        assert result["signal"] == "load-more"
        assert result["text"] == "Load more results"

    def test_show_older_is_not_load_more(self, page: Page) -> None:
        # Prefix anchor: the phrase must *start* with ``load`` or ``show``
        # immediately followed (optional whitespace) by ``more``. ``Show
        # older`` and ``Load previous`` do not qualify.
        _load(
            page,
            "".join(
                [
                    "<button>Show older</button>",
                    "<button>Load previous</button>",
                ]
            ),
        )
        result = _discover(page)
        assert result == {"found": False, "signal": None, "text": None}


class TestNextAllowlist:
    """Signal 2/3 tightening: shared token-allowlist positive/negative cases."""

    @pytest.mark.parametrize(
        "aria_label",
        [
            "View next page",  # BCG (existing win)
            "Next",  # Techwarely / Nearshore text shape via aria too
            "Go to next",  # existing priority test
            "Next page",  # bare N-page form
            "View next jobs",
            "View next 25 jobs",  # digits vanish under tokenization
            "next result",
            "Next opening",
            "Go to the next posting",
        ],
    )
    def test_allowlist_aria_positives(self, page: Page, aria_label: str) -> None:
        _load(page, f'<button aria-label="{aria_label}">›</button>')
        result = _discover(page)
        assert result["signal"] == "aria-label", (
            f"expected aria-label to win on {aria_label!r}, got {result!r}"
        )
        assert result["text"] == aria_label

    @pytest.mark.parametrize(
        "aria_label",
        [
            "Next office",  # Svitla carousel — the load-bearing rejection
            "Next review",  # Svitla carousel
            "Next step of the wizard",  # ``wizard`` / ``step`` not in allowlist
            "View previous page",  # no ``next`` token at all
            "Nextcloud",  # single token ``nextcloud`` ≠ ``next``
        ],
    )
    def test_allowlist_aria_negatives(self, page: Page, aria_label: str) -> None:
        # Isolate signal 2 by placing the label on the only candidate;
        # signals 1, 4-6 cannot fire on a bare button with no class or
        # numeric siblings, and signal 3 cannot match this aria-label
        # via textContent either. So a rejection cascades to found=False.
        _load(page, f'<button aria-label="{aria_label}">›</button>')
        result = _discover(page)
        assert result == {"found": False, "signal": None, "text": None}, (
            f"expected no match on {aria_label!r}, got {result!r}"
        )

    @pytest.mark.parametrize(
        "text",
        [
            "Next",  # bare — the classic Techwarely / Nearshore shape
            "next",  # lowercase
            "NEXT »",  # trailing chevron punctuation vanishes
            "Next page",
            "Go to next",
            "View next jobs",
        ],
    )
    def test_allowlist_text_positives(self, page: Page, text: str) -> None:
        _load(page, f'<a href="/p2">{text}</a>')
        result = _discover(page)
        assert result["signal"] == "text-next", (
            f"expected text-next on {text!r}, got {result!r}"
        )

    @pytest.mark.parametrize(
        "text",
        [
            "Nextcloud",  # single token ≠ ``next``
            "Next office",
            "Next review",
            "Continue",  # no ``next`` token
        ],
    )
    def test_allowlist_text_negatives(self, page: Page, text: str) -> None:
        _load(page, f'<a href="/p2">{text}</a>')
        result = _discover(page)
        assert result == {"found": False, "signal": None, "text": None}, (
            f"expected no match on {text!r}, got {result!r}"
        )


class TestPriorityOrderExtended:
    """Priority tests for the SYS-11 signals on top of the pre-existing chain."""

    def test_numeric_successor_beats_class_next(self, page: Page) -> None:
        # Both a class-next affordance and a signal-4 shape are present
        # in the same DOM. The numeric-successor rule fires first.
        _load(
            page,
            "".join(
                [
                    "<nav>",
                    "<span>1</span>",
                    '<a href="?p=2">2</a>',
                    '<a class="next" href="?p=2">›</a>',
                    "</nav>",
                ]
            ),
        )
        result = _discover(page)
        assert result["signal"] == "numeric-successor"

    def test_class_next_beats_load_more(self, page: Page) -> None:
        # Both signals 5 and 6 fire on the same DOM; signal 5 wins.
        _load(
            page,
            "".join(
                [
                    '<a class="next" href="?p=2">›</a>',
                    "<button>Load More</button>",
                ]
            ),
        )
        result = _discover(page)
        assert result["signal"] == "class-next"

    def test_text_next_beats_load_more(self, page: Page) -> None:
        # A board that provides both a real ``Next`` (text) and a ``Load
        # More`` — the higher signal wins. Ticket edge case.
        _load(
            page,
            "".join(
                [
                    '<a href="?p=2">Next</a>',
                    "<button>Load More</button>",
                ]
            ),
        )
        result = _discover(page)
        assert result["signal"] == "text-next"


class TestExclusionsSys11:
    """SYS-11 signals must respect the same visibility / disabled gates."""

    def test_hidden_class_next_anchor_is_skipped(self, page: Page) -> None:
        _load(
            page,
            '<a class="next" style="display: none" href="/p2">›</a>',
        )
        result = _discover(page)
        assert result == {"found": False, "signal": None, "text": None}

    def test_disabled_class_next_button_is_skipped(self, page: Page) -> None:
        _load(page, '<button class="next" disabled>›</button>')
        result = _discover(page)
        assert result == {"found": False, "signal": None, "text": None}

    def test_hidden_load_more_button_is_skipped(self, page: Page) -> None:
        _load(page, '<button style="display: none">Load More</button>')
        result = _discover(page)
        assert result == {"found": False, "signal": None, "text": None}

    def test_aria_disabled_load_more_is_skipped(self, page: Page) -> None:
        _load(page, '<button aria-disabled="true">Load More</button>')
        result = _discover(page)
        assert result == {"found": False, "signal": None, "text": None}


# ---------------------------------------------------------------------------
# SYS-11 byte-identity sweep: HEAD dry-run descriptors over every state
# file of every paginated fixture. These expected values were recorded
# from HEAD (commit a6155aa4bfc519e168471a65edf34621e919baba, immediately
# before the SYS-11 asset change) and pin the invariant that no
# pre-existing discovery win shifts under the tightening / new signals.
# ---------------------------------------------------------------------------

_FIXTURES_ROOT = Path(__file__).parent.parent / "fixtures" / "snapshots"


# Each entry is (fixture_slug, relative_file, expected_descriptor).
# ``page.html`` is state 1; every ``pages/page-N.html`` is state N.
# Recording commit: a6155aa4bfc519e168471a65edf34621e919baba. If this
# list needs updating, re-run ``scripts/_record_head_discovery.py`` (a
# one-shot helper kept only in local memory of this ticket — do not
# commit) against ``git show HEAD:...`` of the asset.
_SWEEP_EXPECTED: list[tuple[str, str, dict[str, Any]]] = [
    # Techwarely — Prev/Next anchor pagination on a TanStack-Router SPA.
    # States 1 and 2 win on signal 3 (bare "Next"); state 3 is the last
    # page and the ``<a>`` becomes a de-anchored ``<span>Next</span>``,
    # so nothing fires.
    (
        "techwarely",
        "page.html",
        {"found": True, "signal": "text-next", "text": "Next"},
    ),
    (
        "techwarely",
        "pages/page-2.html",
        {"found": True, "signal": "text-next", "text": "Next"},
    ),
    (
        "techwarely",
        "pages/page-3.html",
        {"found": False, "signal": None, "text": None},
    ),
    # Nearshore Business Solutions — same Prev/Next anchor shape.
    (
        "nearshore_business_solutions",
        "page.html",
        {"found": True, "signal": "text-next", "text": "Next"},
    ),
    (
        "nearshore_business_solutions",
        "pages/page-2.html",
        {"found": True, "signal": "text-next", "text": "Next"},
    ),
    (
        "nearshore_business_solutions",
        "pages/page-3.html",
        {"found": False, "signal": None, "text": None},
    ),
    # APM Terminals — aria-label "next" on every non-last state. Under
    # the SYS-11 tightening this remains a signal-2 win because ``next``
    # tokenizes to the single token ``[next]`` which trivially satisfies
    # the allowlist. State 6 is the last page and the control disappears.
    (
        "apm_terminals",
        "page.html",
        {"found": True, "signal": "aria-label", "text": "next"},
    ),
    (
        "apm_terminals",
        "pages/page-2.html",
        {"found": True, "signal": "aria-label", "text": "next"},
    ),
    (
        "apm_terminals",
        "pages/page-3.html",
        {"found": True, "signal": "aria-label", "text": "next"},
    ),
    (
        "apm_terminals",
        "pages/page-4.html",
        {"found": True, "signal": "aria-label", "text": "next"},
    ),
    (
        "apm_terminals",
        "pages/page-5.html",
        {"found": True, "signal": "aria-label", "text": "next"},
    ),
    (
        "apm_terminals",
        "pages/page-6.html",
        {"found": False, "signal": None, "text": None},
    ),
]


def _inject_base_href_local(html: str, base_url: str) -> str:
    """Vendored copy of the harness's ``_inject_base_href``.

    Duplicated verbatim from ``tests/snapshots/test_extractor_snapshots.py``
    so this sweep does not create an import edge into the parametrized
    matcher-count harness. Kept intentionally trivial — see the source
    for the base-tag precedence rationale.
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


class TestPaginatedFixtureSweep:
    """Byte-identity of discovery descriptors across the paginated corpus.

    Any pre-SYS-11 winner shifting under the new signals or the tightened
    allowlist would be a signal regression: this sweep is the executable
    form of the ticket's dry-run byte-identity acceptance criterion, and
    persists as a standing regression guard for every future discovery
    change (extend ``_SWEEP_EXPECTED`` whenever a new paginated fixture
    lands and re-record if a deliberate discovery change moves the
    baseline).
    """

    @pytest.mark.parametrize(
        ("slug", "relpath", "expected"),
        [(s, r, e) for s, r, e in _SWEEP_EXPECTED],
        ids=[f"{s}:{r}" for s, r, _ in _SWEEP_EXPECTED],
    )
    def test_dry_run_descriptor_matches_head(
        self,
        page: Page,
        slug: str,
        relpath: str,
        expected: dict[str, Any],
    ) -> None:
        fixture_dir = _FIXTURES_ROOT / slug
        html = (fixture_dir / relpath).read_text(encoding="utf-8")

        # Resolve the URL the fixture would render under: ``page.html``
        # is the top ``job_board_url``; every ``pages/*`` entry maps to
        # its own ``metadata.pages[i].url``. This matters only for
        # ``checkVisibility`` semantics — discovery does not consult
        # origin — but keeping it aligned with the harness / walker
        # means the sweep exercises the same base-tag path.
        metadata = json.loads(
            (fixture_dir / "metadata.json").read_text(encoding="utf-8")
        )
        if relpath == "page.html":
            base_url = metadata["job_board_url"]
        else:
            pages: list[dict[str, str]] = metadata.get("pages", [])
            match = next((p for p in pages if p["file"] == relpath), None)
            assert match is not None, (
                f"metadata.pages missing entry for {relpath} in {slug}"
            )
            base_url = match["url"]

        page.set_content(
            _inject_base_href_local(html, base_url),
            wait_until="domcontentloaded",
        )
        actual = _discover(page)
        assert actual == expected, (
            f"discovery descriptor drift for {slug}:{relpath}: "
            f"expected {expected!r}, got {actual!r}"
        )
