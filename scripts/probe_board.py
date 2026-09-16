#!/usr/bin/env python3
"""Board profiler — a diagnostic that classifies a career site before configuring.

Every entry in ``blockers/INTEGRATION_BLOCKERS.md`` was produced by hand-driving
the same diagnostic checklist through Playwright MCP: count anchors per
candidate prefix, check frames and their origins, check shadow roots, probe
visibility, look for pagination affordances, inventory filter controls,
fingerprint the ATS, and watch the network for JSON job APIs. That checklist
is mechanisable — this script is the mechanisation.

Usage (from the repo root, so ``vacantes`` is importable)::

    uv run python scripts/probe_board.py -u <job_board_url> -s <sample_job_url>
    uv run python scripts/probe_board.py -u <url> -s <url> --json > report.json

The probe renders the board once in headless Chromium with the same launch
arguments the production agent uses, then emits a structured report covering
seven sections:

    1. Anchor census per candidate prefix (derived + each ancestor) —
       matcher-truth headline from ``EXTRACT_JOB_LINKS_JS`` alongside
       probe-owned diagnostics (visibility split, piercing delta, fragment
       counts, depth histogram). C8/C9 signals.
    2. Frame map — every child frame, its origin, whether it is
       runtime-reachable (same-origin ancestor chain), and a per-document
       diagnostic anchor census. Cross-origin frames are still probed
       driver-side — that is the C5 signal. Frame-recursion is disabled
       inside the diagnostic so the frame counts are attribution, not
       double-counting.
    3. Shadow-DOM census — open-shadow-root count and how many additional
       prefix-matching anchors piercing recovers (C6 signal).
    4. Pagination affordances — ``FIND_NEXT_CONTROL_JS`` in ``dryRun`` mode
       (no marker, no click). Zero new discovery logic (C7 signal).
    5. Filter-control census — native ``<select>`` elements with location
       tokens (filter-token list), ``[role=combobox]`` wrappers, checkbox
       facet clusters, bare search boxes (C2/C3 signals).
    6. Platform fingerprints — a table-driven signal matcher against
       (host, DOM, network) for the evidenced platforms (Greenhouse, Lever,
       Workday, Phenom, Coveo, iCIMS, Ashby, BambooHR); plus any generic
       JSON-with-job-looking-payload response reported as an unclassified
       job-API candidate.
    7. Suggested ``Company(...)`` entry — name/urls echoed back with a
       proposed link rule (prefix + optional ``min_depth``), strategy,
       and paginate flag, plus plain-language notes when the signals
       point at a known blocker class rather than a clean integration.

The probe **suggests**; it never decides. Every report — text and ``--json``
alike — carries a standing caveat that the probe renders once and drives
nothing (no filter clicks, no accordion expansions), so a C3- or C4-shaped
board will surface as a filter-census finding rather than as a post-filter
anchor count.

The census headline is produced by the production matcher asset
(``EXTRACT_JOB_LINKS_JS``); the diagnostic detail (visibility split,
piercing delta, depth histogram, fragment counts) comes from a probe-owned
inline JS analogous to ``_BAKE_AND_SERIALIZE_JS`` in
``scripts/capture_snapshot.py``. The two sources are labelled distinctly in
the report so a reader can trace any number back to its origin.

This module is import-safe: all side effects are gated inside ``main()``.
Pure functions (fingerprint evaluation, min_depth heuristic, prefix
ancestry, report/JSON shape) live at module level with full type
annotations and are exercised by ``tests/unit/test_probe_logic.py`` via
``importlib``. Browser-dependent detectors are exercised against synthetic
``set_content`` pages in ``tests/snapshots/test_probe_census.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

# Repo-root import: this script lives under ``scripts/`` but expects to be
# run with ``uv run python scripts/probe_board.py``, which puts the repo
# root on ``sys.path`` via ``src`` layout registration in ``pyproject.toml``.
from playwright.async_api import Frame, Page, Response, async_playwright

from vacantes.extraction.dom import (
    EXTRACT_JOB_LINKS_JS,
    FIND_NEXT_CONTROL_JS,
)
from vacantes.extraction.dom.rules import derive_path_prefix
from vacantes.settings import launch_capture_browser, plausible_headless_ua

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default per-run wait after navigation for JS rendering. Matches the
# capture script's default so a probe and a capture see the same DOM.
DEFAULT_WAIT_S: int = 8

# Default number of viewport-height scrolls after the wait, ditto.
DEFAULT_SCROLL_N: int = 3

# The filter-token list. Kept here as a module-level constant so
# tests can import it, and so it stays in sync with the token list
# recorded in ``navigation/prompt.py``. Any change to that runtime list
# should be mirrored here or a filter that the agent would recognise
# will not surface in the probe.
LOCATION_TOKENS: tuple[str, ...] = (
    "country",
    "location",
    "region",
    "office",
    "city",
    "pais",
    "país",
)

# Sample-option cap when reporting a native ``<select>``'s options in the
# filter census. Boards with hundreds of country options should not blow
# out the report; ten is enough to see whether Costa Rica is exposed.
SELECT_SAMPLE_OPTION_CAP: int = 10

# The URL substring patterns that indicate an "unclassified job API
# candidate" — used when no platform fingerprint matched but a JSON
# response's URL looks job-related. Deliberately narrow to avoid false
# positives from unrelated tracking / analytics endpoints.
_UNCLASSIFIED_JOB_URL_TOKENS: tuple[str, ...] = (
    "job",
    "jobs",
    "career",
    "careers",
    "posting",
    "postings",
    "opening",
    "openings",
    "requisition",
    "requisitions",
)


# ---------------------------------------------------------------------------
# Fingerprint table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fingerprint:
    """A single ATS / platform fingerprint definition.

    Attributes:
        platform: Human-readable platform name shown in the report.
        host_patterns: Substrings tested case-insensitively against the
            rendered URL's hostname. Any hit fires the ``host`` signal.
        dom_selectors: CSS selectors evaluated in the top document via
            ``document.querySelector``. Any hit fires the ``dom`` signal.
            Kept selector-only (no attribute-value assertions beyond the
            selector itself) so each definition is trivially testable in
            isolation.
        network_patterns: Substrings tested case-insensitively against
            observed response URLs during the render window. Any hit
            fires the ``network`` signal.
        strategy_suggestion: The ``Company.strategy`` value implied by
            this fingerprint. Only "greenhouse" maps to a shipped
            adapter today; everything else maps to "dom" — the
            fingerprint is reported for a human's read of the report,
            not as a config-time decision.
    """

    platform: str
    host_patterns: tuple[str, ...]
    dom_selectors: tuple[str, ...]
    network_patterns: tuple[str, ...]
    strategy_suggestion: str


FINGERPRINT_TABLE: tuple[Fingerprint, ...] = (
    Fingerprint(
        platform="Greenhouse",
        host_patterns=(
            "job-boards.greenhouse.io",
            "boards.greenhouse.io",
        ),
        dom_selectors=(
            "[data-gh_jid]",
            ".grnhse_iframe",
            "#grnhse_iframe",
            "iframe[src*='greenhouse.io']",
        ),
        network_patterns=("boards-api.greenhouse.io",),
        strategy_suggestion="greenhouse",
    ),
    Fingerprint(
        platform="Lever",
        host_patterns=("jobs.lever.co",),
        dom_selectors=("iframe[src*='jobs.lever.co']",),
        network_patterns=("api.lever.co",),
        strategy_suggestion="dom",
    ),
    Fingerprint(
        platform="Workday",
        host_patterns=("myworkdayjobs.com", "myworkday.com"),
        dom_selectors=("[data-automation-id='jobResults']",),
        network_patterns=("myworkdayjobs.com", "wday/cxs"),
        strategy_suggestion="dom",
    ),
    Fingerprint(
        platform="Phenom",
        host_patterns=("phenompeople.com",),
        # ``phw-*`` custom elements are Phenom's Aurelia widget tags;
        # any board embedding Phenom carries one.
        dom_selectors=(
            "[class^='phw-']",
            "[class*=' phw-']",
            "phw-app",
        ),
        network_patterns=("/widgets", "phenompeople.com"),
        strategy_suggestion="dom",
    ),
    Fingerprint(
        platform="Coveo",
        host_patterns=(".coveo.com",),
        dom_selectors=(
            "atomic-search-interface",
            "[data-coveo]",
        ),
        network_patterns=("/rest/search/v2", ".coveo.com"),
        strategy_suggestion="dom",
    ),
    Fingerprint(
        platform="iCIMS",
        host_patterns=("icims.com",),
        dom_selectors=(
            "#icims_content_iframe",
            "iframe[src*='icims.com']",
        ),
        network_patterns=("icims.com",),
        strategy_suggestion="dom",
    ),
    Fingerprint(
        platform="Ashby",
        host_patterns=("jobs.ashbyhq.com", "ashbyhq.com"),
        dom_selectors=("iframe[src*='ashbyhq.com']",),
        network_patterns=("ashbyhq.com",),
        strategy_suggestion="dom",
    ),
    Fingerprint(
        platform="BambooHR",
        host_patterns=(".bamboohr.com",),
        dom_selectors=("iframe[src*='bamboohr.com']",),
        network_patterns=("bamboohr.com",),
        strategy_suggestion="dom",
    ),
    Fingerprint(
        platform="SmartRecruiters",
        host_patterns=("smartrecruiters.com",),
        dom_selectors=("iframe[src*='smartrecruiters.com']",),
        network_patterns=("smartrecruiters.com",),
        strategy_suggestion="dom",
    ),
    Fingerprint(
        platform="RecruitCRM",
        host_patterns=("recruitcrm.io",),
        dom_selectors=(),
        network_patterns=("recruitcrm.io",),
        strategy_suggestion="dom",
    ),
    Fingerprint(
        platform="Simplicant",
        host_patterns=(".simplicant.com",),
        dom_selectors=(),
        network_patterns=(".simplicant.com",),
        strategy_suggestion="dom",
    ),
    Fingerprint(
        platform="TeamTailor",
        host_patterns=("teamtailor.com",),
        dom_selectors=(),
        network_patterns=("teamtailor.com",),
        strategy_suggestion="dom",
    ),
)


# ---------------------------------------------------------------------------
# Probe-owned diagnostic JS
#
# Runs the same visibility and prefix logic the matcher does, but returns
# aggregates rather than the URL list. Two passes (piercing on / off) let
# us compute the shadow-recovery delta. Frame recursion is intentionally
# off — the caller runs this per frame scope so each frame's numbers are
# attributed to that frame rather than double-counted upstream.
# ---------------------------------------------------------------------------
_CENSUS_JS = r"""
([basePath, careerOrigin, minDepth]) => {
    const isVisible = (el) => {
        if (typeof el.checkVisibility === 'function') {
            return el.checkVisibility({ checkVisibilityCSS: true });
        }
        if (el.offsetParent !== null) return true;
        try { return getComputedStyle(el).position === 'fixed'; }
        catch (e) { return false; }
    };

    // Collect anchors from the current document only. Piercing = descend
    // into every open shadow root. No frame recursion — the caller drives
    // per-frame invocation instead.
    const collectAnchors = (pierceShadow) => {
        const anchors = [];
        const seenRoots = new WeakSet();
        const walk = (root) => {
            if (!root || seenRoots.has(root)) return;
            seenRoots.add(root);
            let elements;
            try { elements = root.querySelectorAll('*'); }
            catch (e) { return; }
            for (const el of elements) {
                if (el.tagName === 'A') anchors.push(el);
                if (pierceShadow && el.shadowRoot) walk(el.shadowRoot);
            }
        };
        walk(document);
        return anchors;
    };

    const countOpenShadowRoots = () => {
        let count = 0;
        const seen = new WeakSet();
        const walk = (root) => {
            if (!root || seen.has(root)) return;
            seen.add(root);
            let els;
            try { els = root.querySelectorAll('*'); }
            catch (e) { return; }
            for (const el of els) {
                if (el.shadowRoot) { count++; walk(el.shadowRoot); }
            }
        };
        walk(document);
        return count;
    };

    // Analyse a set of anchors against (basePath, careerOrigin, minDepth).
    // Returns aggregates only — never per-anchor dumps, so very large
    // boards do not inflate the JSON payload.
    const analyse = (anchors) => {
        let eligible_incl_hidden = 0;
        let matcher_visible = 0;
        let hidden_count = 0;
        let fragment_self_links = 0;
        let fragment_variants = 0;
        const depth_histogram = {};
        const seenCanonical = new Set();
        for (const a of anchors) {
            const href = a.href || '';
            if (!href) continue;
            let linkUrl;
            try { linkUrl = new URL(href); }
            catch (e) { continue; }
            if (linkUrl.origin !== careerOrigin) continue;

            const hadFragment = linkUrl.hash.length > 0;
            linkUrl.hash = '';
            const linkPath = linkUrl.pathname.replace(/\/$/, '');

            let inPathBranch = false;
            let depth = 0;
            if (linkPath.startsWith(basePath + '/')) {
                inPathBranch = true;
                const remainder = linkPath.slice(basePath.length + 1);
                depth = remainder.split('/').length;
            } else if (linkPath === basePath && linkUrl.search.length > 0) {
                inPathBranch = false;
            } else {
                continue;
            }

            eligible_incl_hidden++;
            if (hadFragment) fragment_self_links++;
            const canonical = linkUrl.href;
            if (seenCanonical.has(canonical)) {
                fragment_variants++;
            } else {
                seenCanonical.add(canonical);
            }

            const visible = isVisible(a);
            if (!visible) { hidden_count++; continue; }

            if (inPathBranch) {
                const key = String(depth);
                depth_histogram[key] = (depth_histogram[key] || 0) + 1;
                if (depth < minDepth) continue;
            }
            matcher_visible++;
        }
        return {
            eligible_incl_hidden,
            matcher_visible,
            hidden_count,
            fragment_self_links,
            fragment_variants,
            depth_histogram,
        };
    };

    const withShadow = analyse(collectAnchors(true));
    const lightOnly = analyse(collectAnchors(false));

    return {
        eligible_incl_hidden: withShadow.eligible_incl_hidden,
        matcher_visible: withShadow.matcher_visible,
        light_dom_matcher_visible: lightOnly.matcher_visible,
        piercing_recovery:
            withShadow.matcher_visible - lightOnly.matcher_visible,
        hidden_count: withShadow.hidden_count,
        fragment_self_links: withShadow.fragment_self_links,
        fragment_variants: withShadow.fragment_variants,
        depth_histogram: withShadow.depth_histogram,
        open_shadow_root_count: countOpenShadowRoots(),
    };
}
"""


# ---------------------------------------------------------------------------
# Probe-owned filter-census JS
# ---------------------------------------------------------------------------
_FILTER_CENSUS_JS = r"""
([tokens, sampleOptionCap]) => {
    const isVisible = (el) => {
        if (typeof el.checkVisibility === 'function') {
            return el.checkVisibility({ checkVisibilityCSS: true });
        }
        if (el.offsetParent !== null) return true;
        try { return getComputedStyle(el).position === 'fixed'; }
        catch (e) { return false; }
    };

    // Lowercase-tokenise a string. Anything that hits a token is a
    // location-shaped filter candidate.
    const tokenLower = tokens.map((t) => t.toLowerCase());
    const matchesToken = (s) => {
        if (!s) return false;
        const low = s.toLowerCase();
        return tokenLower.some((t) => low.includes(t));
    };

    // Native <select> elements. Match on name / id / associated <label>.
    const selects = [];
    for (const el of document.querySelectorAll('select')) {
        if (!isVisible(el)) continue;
        const name = el.getAttribute('name') || '';
        const id = el.getAttribute('id') || '';
        let labelText = '';
        if (id) {
            const label = document.querySelector('label[for="' + id + '"]');
            if (label) labelText = (label.textContent || '').trim();
        }
        if (!matchesToken(name) && !matchesToken(id) &&
            !matchesToken(labelText)) {
            continue;
        }
        const options = [];
        for (const opt of el.querySelectorAll('option')) {
            if (options.length >= sampleOptionCap) break;
            const text = (opt.textContent || '').trim();
            if (text) options.push(text);
        }
        selects.push({
            kind: 'select',
            tag: 'select',
            name: name || null,
            id: id || null,
            label: labelText || null,
            sample_options: options,
        });
    }

    // ARIA comboboxes — a wrapper or trigger with role=combobox, or a
    // button whose aria-haspopup indicates a listbox. This is the shape
    // that Deel and Zencore's location filters take, and that legacy
    // GOAL_PROMPT already covered.
    const comboboxes = [];
    for (const el of document.querySelectorAll(
        '[role="combobox"], [aria-haspopup="listbox"]'
    )) {
        if (!isVisible(el)) continue;
        const text = (
            el.getAttribute('aria-label') ||
            el.textContent || ''
        ).trim().slice(0, 200);
        comboboxes.push({
            kind: 'combobox',
            tag: el.tagName.toLowerCase(),
            name: el.getAttribute('name') || null,
            id: el.getAttribute('id') || null,
            label: text || null,
            sample_options: [],
        });
    }

    // Checkbox facet groups — a container element with ≥2 checkbox
    // descendants and a nearby label matching a location token.
    const checkboxGroups = [];
    const seenContainers = new WeakSet();
    for (const cb of document.querySelectorAll(
        'input[type="checkbox"]'
    )) {
        if (!isVisible(cb)) continue;
        // Walk up until we find a container with ≥2 checkboxes.
        let container = cb.parentElement;
        let hops = 0;
        while (container && hops < 6) {
            const count = container.querySelectorAll(
                'input[type="checkbox"]'
            ).length;
            if (count >= 2) break;
            container = container.parentElement;
            hops++;
        }
        if (!container || seenContainers.has(container)) continue;
        seenContainers.add(container);
        // Nearest label / heading text for token matching.
        const nearby = (container.textContent || '').trim().slice(0, 200);
        if (!matchesToken(nearby)) continue;
        // Extract a short label sample from labels attached to the
        // container's checkboxes.
        const sample = [];
        for (const inner of container.querySelectorAll(
            'input[type="checkbox"]'
        )) {
            if (sample.length >= sampleOptionCap) break;
            const iid = inner.id;
            let itext = '';
            if (iid) {
                const lbl = document.querySelector(
                    'label[for="' + iid + '"]'
                );
                if (lbl) itext = (lbl.textContent || '').trim();
            }
            if (!itext) {
                const parentLbl = inner.closest('label');
                if (parentLbl) {
                    itext = (parentLbl.textContent || '').trim();
                }
            }
            if (itext) sample.push(itext);
        }
        checkboxGroups.push({
            kind: 'checkbox_group',
            tag: container.tagName.toLowerCase(),
            name: null,
            id: container.getAttribute('id') || null,
            label: nearby.slice(0, 80) || null,
            sample_options: sample,
        });
    }

    // Bare search boxes. Any visible text/search input whose accessible
    // name is not location-shaped. Reported for the C2 shape ("board
    // exposes only a text search").
    const searchBoxes = [];
    for (const el of document.querySelectorAll(
        'input[type="search"], input[type="text"]'
    )) {
        if (!isVisible(el)) continue;
        const placeholder = el.getAttribute('placeholder') || '';
        const ariaLabel = el.getAttribute('aria-label') || '';
        const nameAttr = el.getAttribute('name') || '';
        const idAttr = el.getAttribute('id') || '';
        // Skip fields that already look location-shaped — they belong
        // in one of the buckets above, and reporting them twice would
        // muddle the C2 vs C3 signal.
        if (matchesToken(placeholder) || matchesToken(ariaLabel) ||
            matchesToken(nameAttr) || matchesToken(idAttr)) {
            continue;
        }
        searchBoxes.push({
            kind: 'search_box',
            tag: 'input',
            name: nameAttr || null,
            id: idAttr || null,
            label: (
                ariaLabel || placeholder || ''
            ).slice(0, 80) || null,
            sample_options: [],
        });
    }

    return {
        selects,
        comboboxes,
        checkbox_groups: checkboxGroups,
        search_boxes: searchBoxes,
    };
}
"""


# ---------------------------------------------------------------------------
# Probe-owned DOM-marker JS (fingerprints)
# ---------------------------------------------------------------------------
_DOM_MARKERS_JS = r"""
(selectors) => {
    const hits = [];
    for (const sel of selectors) {
        let found = false;
        try {
            found = document.querySelector(sel) !== null;
        } catch (e) {
            // Malformed selector — treat as "no hit" rather than
            // failing the whole probe.
            found = false;
        }
        if (found) hits.push(sel);
    }
    return hits;
}
"""


# ---------------------------------------------------------------------------
# Pure functions (unit-tested via importlib)
# ---------------------------------------------------------------------------


def build_prefix_ancestry(sample_job_url: str) -> list[str]:
    """Return the derived prefix plus every one of its path ancestors.

    Example: ``/company/careers/eng/1234-senior`` derives ``/company/careers/eng``
    and expands to ``["/company/careers/eng", "/company/careers", "/company", "/"]``.
    The root ``"/"`` is always included as the outermost ancestor.

    Duplicates are collapsed: a sample that already derives to ``"/"`` returns
    ``["/"]``, not ``["/", "/"]``.
    """
    derived = derive_path_prefix(sample_job_url)
    ancestors: list[str] = []
    current = derived
    seen: set[str] = set()
    while True:
        if current not in seen:
            ancestors.append(current)
            seen.add(current)
        if current == "/":
            break
        # posixpath-style parent computation — every "/a/b/c" → "/a/b" → "/a" → "/".
        parent = current.rsplit("/", 1)[0]
        current = parent if parent else "/"
    return ancestors


def sample_tail_depth(sample_job_url: str, prefix: str) -> int:
    """Return the number of path segments the sample URL has under ``prefix``.

    ``sample_job_url`` ``/company/careers/eng/1234-senior`` under prefix
    ``/company/careers`` returns 2 (``eng`` + ``1234-senior``). Under prefix
    ``/company`` it returns 3. Under a prefix the sample does not start with,
    returns 0.
    """
    path = urlparse(sample_job_url).path.rstrip("/")
    if prefix == "/":
        # Every path descends from root; count all non-empty segments.
        segments = [s for s in path.split("/") if s]
        return len(segments)
    if not path.startswith(prefix + "/"):
        return 0
    remainder = path[len(prefix) + 1 :]
    return len(remainder.split("/"))


def suggest_min_depth(
    sample_tail: int,
    depth_histogram: dict[int, int],
) -> int | None:
    """Suggest a ``LinkRule.min_depth`` floor based on the depth histogram.

    Heuristic: with ``D = sample_tail``, if any anchors matched at depths
    ``< D``, return ``max(shallow_depth) + 1`` — the smallest floor that
    excludes the shallow cluster without cutting the sample's own depth
    (or shallower postings whose depth == D). Returns ``None`` when there
    is no shallow cluster to exclude, or when the sample sits at depth 0
    (a query-branch board, where ``min_depth`` is not consulted).

    Reproduces the shipped guidance for the two C9 boards:

    - Databricks: sample depth 2, chrome at depth 1 → suggests ``2``.
    - Avionyx: sample depth 3, chrome at depth 1 → suggests ``2``.
    """
    if sample_tail <= 0:
        return None
    shallow_depths = [d for d in depth_histogram if 0 < d < sample_tail]
    if not shallow_depths:
        return None
    return max(shallow_depths) + 1


@dataclass(frozen=True)
class FingerprintHit:
    """A resolved fingerprint match with the signal types that fired.

    ``signals`` is a list of ``"host"``, ``"dom"``, or ``"network"``
    entries — one per axis that fired for the platform. The list is
    deduplicated and stable-ordered so the JSON output is diff-friendly
    across probe runs against the same board.
    """

    platform: str
    signals: tuple[str, ...]
    strategy_suggestion: str


def evaluate_fingerprints(
    hostname: str,
    dom_hits_by_selector: dict[str, bool],
    network_urls: list[str],
    table: tuple[Fingerprint, ...] = FINGERPRINT_TABLE,
) -> list[FingerprintHit]:
    """Evaluate the fingerprint table against the three signal axes.

    ``dom_hits_by_selector`` maps every selector *from the table* to
    whether it matched in the DOM. Callers precompute this map so the
    browser-side evaluate is a single round-trip; passing it as data
    keeps the evaluator pure and unit-testable without a browser.
    ``network_urls`` is the flat list of response URLs observed during
    the render window (order-preserving; may contain duplicates).

    Returns one :class:`FingerprintHit` per platform that fired on any
    axis, in table order. Platforms that fired on zero axes are omitted.
    """
    hostname_low = hostname.lower()
    hits: list[FingerprintHit] = []
    for fp in table:
        signals: list[str] = []
        if any(h.lower() in hostname_low for h in fp.host_patterns):
            signals.append("host")
        if any(dom_hits_by_selector.get(sel, False) for sel in fp.dom_selectors):
            signals.append("dom")
        if any(
            any(np.lower() in url.lower() for np in fp.network_patterns)
            for url in network_urls
        ):
            signals.append("network")
        if signals:
            hits.append(
                FingerprintHit(
                    platform=fp.platform,
                    signals=tuple(signals),
                    strategy_suggestion=fp.strategy_suggestion,
                )
            )
    return hits


def find_unclassified_job_apis(
    network_urls: list[str],
    fingerprint_hits: list[FingerprintHit],
    table: tuple[Fingerprint, ...] = FINGERPRINT_TABLE,
) -> list[str]:
    """Return network URLs that look job-related but no fingerprint matched.

    Any URL whose path contains one of the :data:`_UNCLASSIFIED_JOB_URL_TOKENS`
    substrings is a candidate. URLs already attributed to a fingerprint (by
    substring match against that fingerprint's ``network_patterns``) are
    filtered out so a Greenhouse API URL does not surface twice.

    Order-preserving deduplication: the first occurrence of each URL wins.
    """
    hit_platforms = {h.platform for h in fingerprint_hits}
    claimed_patterns: list[str] = []
    for fp in table:
        if fp.platform in hit_platforms:
            claimed_patterns.extend(p.lower() for p in fp.network_patterns)

    seen: set[str] = set()
    candidates: list[str] = []
    for url in network_urls:
        if url in seen:
            continue
        low = url.lower()
        # Attributed to a fingerprint that already fired → skip.
        if any(cp in low for cp in claimed_patterns):
            continue
        # Path-level token match (avoid matching a `job-search` host name
        # in a totally unrelated request by scoping to the URL's path).
        try:
            path_low = urlparse(url).path.lower()
        except ValueError:
            continue
        if not any(tok in path_low for tok in _UNCLASSIFIED_JOB_URL_TOKENS):
            continue
        candidates.append(url)
        seen.add(url)
    return candidates


def build_suggestion(
    *,
    company_snippet_name: str,
    job_board_url: str,
    sample_job_url: str,
    derived_prefix: str,
    min_depth_suggestion: int | None,
    min_depth_prefix: str | None,
    fingerprint_hits: list[FingerprintHit],
    paginate_found: bool,
    hidden_heavy: bool,
    zero_anchor_with_api: bool,
    cross_origin_frame_with_anchors: bool,
    filter_findings_summary: str | None,
) -> dict[str, Any]:
    """Assemble the report's section-7 suggestion payload.

    The output is a plain dict so both the text renderer and the JSON
    mode consume the same structure. ``notes`` is a plain-language list
    of observations that a human should read before applying any
    suggestion; ``caveats`` carries the standing "probe does not drive
    filters" reminder alongside any evidence-specific warnings.

    Suggestion logic (conservative — never write to config; the human
    verifies via the existing ground-truth + snapshot steps):

    - ``strategy``: ``"greenhouse"`` iff a Greenhouse fingerprint fired,
      because that is the only adapter registered today. Every other
      fingerprint reports its platform but still suggests ``"dom"``.
    - ``paginate``: ``True`` iff :func:`FIND_NEXT_CONTROL_JS` dryRun
      reported a control on state 1. Suppressed when
      ``strategy="greenhouse"`` — the API strategy ignores paginate.
    - ``min_depth`` + ``min_depth_prefix``: whatever :func:`suggest_min_depth`
      returned against the best-fit candidate's diagnostic depth
      histogram. If ``min_depth_prefix`` differs from ``derived_prefix``,
      the C9 ancestor-promotion case applies and the LinkRule must carry
      ``path_prefix=<promoted>`` alongside ``min_depth`` — otherwise
      posixpath.dirname on the sample URL would re-derive the too-narrow
      prefix at runtime and lose the depth fix.
    """
    greenhouse_hit = next(
        (h for h in fingerprint_hits if h.platform == "Greenhouse"),
        None,
    )
    strategy = greenhouse_hit.strategy_suggestion if greenhouse_hit else "dom"

    # Compose the ``Company(...)`` snippet. Greenhouse entries do not
    # need link_rule / paginate; DOM entries carry the link_rule only
    # when it deviates from the default.
    lines = [
        "Company(",
        f'    name="{company_snippet_name}",',
        f'    job_board_url="{job_board_url}",',
        f'    sample_job_url="{sample_job_url}",',
    ]
    if strategy == "greenhouse":
        lines.append('    strategy="greenhouse",')
    else:
        if min_depth_suggestion is not None:
            if min_depth_prefix and min_depth_prefix != derived_prefix:
                # C9 ancestor promotion: sample URL alone would derive a
                # too-narrow prefix; pin the LinkRule to the promoted
                # ancestor so both branches of the id-in-path taxonomy
                # (chrome + real jobs) share the depth check's scope.
                lines.append(
                    f'    link_rule=LinkRule(path_prefix="{min_depth_prefix}", '
                    f"min_depth={min_depth_suggestion}),"
                )
            else:
                lines.append(
                    f"    link_rule=LinkRule(min_depth={min_depth_suggestion}),"
                )
        if paginate_found:
            lines.append("    paginate=True,")
    lines.append(")")
    snippet = "\n".join(lines)

    notes: list[str] = []
    if greenhouse_hit is not None:
        notes.append(
            "Greenhouse fingerprint detected — GreenhouseStrategy sidesteps "
            "the DOM path (C2/C5 for Greenhouse tenants). Record an API "
            "payload under tests/fixtures/api/greenhouse/<token>.json and "
            "extend tests/unit/test_greenhouse.py instead of capturing a "
            "snapshot."
        )
    elif fingerprint_hits:
        platforms = ", ".join(h.platform for h in fingerprint_hits)
        notes.append(
            f"Non-Greenhouse platform fingerprint(s) detected: {platforms}. "
            "No dedicated adapter exists today — the DOM strategy is the "
            "default. Watch the anchor census for C1 (zero anchors on a "
            "well-rendered page) or a cross-origin embed (C5)."
        )
    # Skip the paginate note under the greenhouse strategy — the API path
    # ignores pagination on the DOM listing and the note would contradict
    # the emitted snippet (which correctly omits paginate=True).
    if paginate_found and strategy != "greenhouse":
        notes.append(
            "Pagination affordance found on state 1 — set paginate=True and "
            "capture the snapshot with --paginate (C7)."
        )
    if min_depth_suggestion is not None:
        prefix_scope = (
            f"under {min_depth_prefix}"
            if min_depth_prefix and min_depth_prefix != derived_prefix
            else "under the derived prefix"
        )
        notes.append(
            f"Depth-split candidate {prefix_scope} — chrome links live at "
            f"shallow depths while real postings sit at depth "
            f">={min_depth_suggestion}. Applying min_depth here excludes "
            f"the chrome without cutting real postings (C9)."
        )
    if cross_origin_frame_with_anchors:
        notes.append(
            "Cross-origin frame(s) contain anchors — the DOM matcher "
            "runs in-page and cannot reach them (C5). If those anchors "
            "are the real board, a per-platform adapter (see the "
            "Greenhouse precedent) is required."
        )
    if hidden_heavy:
        notes.append(
            "Hidden-anchor share is unusually high on the derived prefix — "
            "the board may be applying a client-side filter via display: "
            "none (C8, closed visibility gate; reported here so a "
            "reader can sanity-check the resulting count)."
        )
    if zero_anchor_with_api:
        notes.append(
            "Zero prefix-matching anchors after render, but a JSON job API "
            "was observed in the network trace (C1 / G4). Consider a "
            "per-platform adapter or investigate whether the anchors are "
            "click-handler-only cards (C1) rather than <a> elements."
        )
    if filter_findings_summary is not None:
        notes.append(
            f"Filter-control census surfaced: {filter_findings_summary}. "
            "The probe does not apply filters — verify with the ground-"
            "truth script and live run whether the agent drives these "
            "controls under GOAL_PROMPT."
        )

    caveats = [
        "This probe renders the board once and drives nothing. Filters are "
        "inventoried, not applied; accordions are not expanded. C3/C4 "
        "boards surface as filter-census findings, not post-filter counts.",
        "Suggestions are never auto-applied. Verify via the ground-truth "
        "script and live run per the integrate-company skill.",
    ]

    return {
        "company_snippet": snippet,
        "strategy": strategy,
        "paginate": paginate_found,
        "min_depth": min_depth_suggestion,
        "notes": notes,
        "caveats": caveats,
    }


# ---------------------------------------------------------------------------
# Async browser probes
# ---------------------------------------------------------------------------


async def _same_origin_ancestor_chain(frame: Frame, top_origin: str) -> bool:
    """Return True iff every ancestor of ``frame`` shares ``top_origin``.

    Mirrors the same-named helper in ``scripts/capture_snapshot.py`` — the
    runtime matcher walks same-origin frames in-page, so any frame with a
    cross-origin ancestor is driver-visible but runtime-unreachable.
    """
    node: Frame | None = frame
    while node is not None and node.parent_frame is not None:
        parsed = urlparse(node.url)
        node_origin = f"{parsed.scheme}://{parsed.netloc}"
        if node_origin != top_origin:
            return False
        node = node.parent_frame
    return True


async def _run_matcher(
    page: Page,
    prefix: str,
    origin: str,
    min_depth: int = 1,
) -> int:
    """Return the URL count the production matcher yields for a candidate.

    Delegates to ``EXTRACT_JOB_LINKS_JS`` with ``[prefix, origin, min_depth]``
    so section 1's headline number is verifiably matcher-truth.
    """
    urls = await page.evaluate(EXTRACT_JOB_LINKS_JS, [prefix, origin, min_depth])
    return len(urls or [])


async def _run_census(
    scope: Page | Frame,
    prefix: str,
    origin: str,
    min_depth: int = 1,
) -> dict[str, Any]:
    """Run the probe-owned diagnostic census against ``scope``."""
    raw: Any = await scope.evaluate(_CENSUS_JS, [prefix, origin, min_depth])
    # Depth histogram comes back with string keys because JS object keys
    # are strings; normalise for the report layer.
    hist_raw = raw.get("depth_histogram") or {}
    depth_hist: dict[int, int] = {int(k): int(v) for k, v in hist_raw.items()}
    return {
        "eligible_incl_hidden": int(raw.get("eligible_incl_hidden", 0)),
        "matcher_visible": int(raw.get("matcher_visible", 0)),
        "light_dom_matcher_visible": int(raw.get("light_dom_matcher_visible", 0)),
        "piercing_recovery": int(raw.get("piercing_recovery", 0)),
        "hidden_count": int(raw.get("hidden_count", 0)),
        "fragment_self_links": int(raw.get("fragment_self_links", 0)),
        "fragment_variants": int(raw.get("fragment_variants", 0)),
        "depth_histogram": depth_hist,
        "open_shadow_root_count": int(raw.get("open_shadow_root_count", 0)),
    }


async def _probe_pagination(page: Page) -> dict[str, Any]:
    """Run ``FIND_NEXT_CONTROL_JS`` in dryRun mode.

    Returns ``{found, signal, control_text}`` with keys renamed to match
    the report shape.
    """
    raw: Any = await page.evaluate(FIND_NEXT_CONTROL_JS, [True])
    if not isinstance(raw, dict):
        return {"found": False, "signal": None, "control_text": None}
    return {
        "found": bool(raw.get("found", False)),
        "signal": raw.get("signal"),
        "control_text": raw.get("text"),
    }


async def _probe_filters(page: Page) -> dict[str, Any]:
    """Run the filter-census JS and normalise its return shape."""
    raw: Any = await page.evaluate(
        _FILTER_CENSUS_JS,
        [list(LOCATION_TOKENS), SELECT_SAMPLE_OPTION_CAP],
    )
    return {
        "selects": list(raw.get("selects") or []),
        "comboboxes": list(raw.get("comboboxes") or []),
        "checkbox_groups": list(raw.get("checkbox_groups") or []),
        "search_boxes": list(raw.get("search_boxes") or []),
    }


async def _probe_dom_markers(page: Page) -> dict[str, bool]:
    """Return a ``{selector: bool}`` map for every fingerprint DOM selector."""
    all_selectors: list[str] = []
    for fp in FINGERPRINT_TABLE:
        all_selectors.extend(fp.dom_selectors)
    if not all_selectors:
        return {}
    hits: list[str] = await page.evaluate(_DOM_MARKERS_JS, all_selectors)
    return {sel: (sel in set(hits)) for sel in all_selectors}


# ---------------------------------------------------------------------------
# Top-level probe orchestration
# ---------------------------------------------------------------------------


@dataclass
class _CandidateSection:
    """Per-prefix section 1 result (matcher headline + diagnostic detail)."""

    prefix: str
    label: str  # "derived" or "ancestor"
    matcher_count: int
    census: dict[str, Any]
    min_depth_suggestion: int | None


async def _probe(
    job_board_url: str,
    sample_job_url: str,
    wait_s: int,
    scroll_n: int,
) -> dict[str, Any]:
    """Render the board once and produce the full report dict.

    The returned dict is the single source of truth for both the text
    renderer and the ``--json`` output — do not compute display strings
    at emit time, so parity between the two modes stays cheap.
    """
    parsed = urlparse(job_board_url)
    top_origin = f"{parsed.scheme}://{parsed.netloc}"
    ancestry = build_prefix_ancestry(sample_job_url)
    derived_prefix = ancestry[0]

    network_urls: list[str] = []
    json_response_urls: list[str] = []

    def _on_response(resp: Response) -> None:
        # Passive observation: capture every response's URL and note
        # whether the response looks like JSON (content-type sniff only,
        # no body read — that would count as issuing a request).
        try:
            network_urls.append(resp.url)
            ct = resp.headers.get("content-type", "") if resp.headers else ""
            if "json" in ct.lower():
                json_response_urls.append(resp.url)
        except Exception:  # noqa: BLE001 — response handler must not raise
            pass

    async with async_playwright() as p:
        browser = await launch_capture_browser(p)
        try:
            # The capture-side launch config lives in
            # ``settings.launch_capture_browser`` so this script and
            # ``probe_board`` cannot drift apart: same channel, same
            # flags, same UA. The plausible UA
            # (``HeadlessChrome/<v>`` → ``Chrome/<v>``) closes the C14
            # WAF-403 class documented in
            # ``blockers/INTEGRATION_BLOCKERS.md``; the channel and the
            # AutomationControlled flag close the two capture-only
            # rejections described in ``settings``.
            user_agent = await plausible_headless_ua()
            page = await browser.new_page(user_agent=user_agent)
            page.on("response", _on_response)
            await page.goto(job_board_url)
            await asyncio.sleep(wait_s)
            for _ in range(scroll_n):
                await page.evaluate("() => { window.scrollBy(0, window.innerHeight); }")
                await asyncio.sleep(1)

            rendered_url = page.url
            rendered_at = datetime.now(tz=UTC).isoformat()

            # Section 1: per-candidate-prefix census. min_depth is
            # evaluated on every candidate, not just the derived prefix —
            # C9 boards (Databricks, Avionyx) derive a too-narrow prefix
            # from the sample URL, and the actionable depth split lives
            # at a wider ancestor. The section-7 assembly then picks the
            # ancestor with the highest matcher_count whose histogram
            # fires the heuristic.
            candidates: list[_CandidateSection] = []
            for i, prefix in enumerate(ancestry):
                matcher_count = await _run_matcher(page, prefix, top_origin, 1)
                census = await _run_census(page, prefix, top_origin, 1)
                tail = sample_tail_depth(sample_job_url, prefix)
                min_depth = suggest_min_depth(tail, census["depth_histogram"])
                candidates.append(
                    _CandidateSection(
                        prefix=prefix,
                        label="derived" if i == 0 else "ancestor",
                        matcher_count=matcher_count,
                        census=census,
                        min_depth_suggestion=min_depth,
                    )
                )

            # Promote to the innermost firing ancestor. The ancestry
            # list is ordered derived→root (narrowest first), so the
            # first candidate whose heuristic fires is the narrowest
            # sufficient scope for the C9 fix. Preferring innermost over
            # widest-matcher-count matches shipped guidance (Databricks:
            # `/company/careers, min_depth=2` rather than `/company,
            # min_depth=3`) — both prune the same anchor set at runtime,
            # but the narrower prefix is safer against future URL-
            # taxonomy shifts (a new `/company/foo` chrome page would
            # not affect the LinkRule).
            promoted_min_depth: int | None = None
            promoted_min_depth_prefix: str | None = None
            for c in candidates:
                if c.min_depth_suggestion is not None:
                    promoted_min_depth = c.min_depth_suggestion
                    promoted_min_depth_prefix = c.prefix
                    break

            # Section 2: frame map. Cross-origin frames are still probed
            # driver-side (that is the C5 signal) but flagged as
            # runtime-unreachable.
            frames_report: list[dict[str, Any]] = []
            cross_origin_with_anchors = False
            for idx, frame in enumerate(page.frames):
                if frame is page.main_frame:
                    continue
                frame_parsed = urlparse(frame.url)
                frame_origin = f"{frame_parsed.scheme}://{frame_parsed.netloc}"
                reachable = await _same_origin_ancestor_chain(frame, top_origin)
                census_count: int | None
                census_note: str | None = None
                try:
                    frame_census = await _run_census(
                        frame,
                        derived_prefix,
                        top_origin,
                        1,
                    )
                    census_count = frame_census["matcher_visible"]
                except Exception:  # noqa: BLE001 — best-effort per frame
                    census_count = None
                    census_note = "census unavailable (frame evaluate failed)"
                # Driver-side placeholder frames (about:blank, srcdoc,
                # javascript: pseudo-URLs) are not real cross-origin
                # surfaces — Playwright reports them as such but the
                # runtime matcher does not care about them and calling
                # them "C5 signals" would mislead a probe reader into
                # requesting a per-platform adapter for a shadow-DOM
                # board (e.g. Team Talent, where the actual anchors are
                # in the top document's shadow tree).
                _placeholder_frame = frame_parsed.scheme in {
                    "about",
                    "data",
                    "javascript",
                    "",
                }
                if (
                    not reachable
                    and not _placeholder_frame
                    and census_count
                    and census_count > 0
                ):
                    cross_origin_with_anchors = True
                frames_report.append(
                    {
                        "index": idx,
                        "url": frame.url,
                        "origin": frame_origin,
                        "runtime_reachable": reachable,
                        "census_count": census_count,
                        "note": census_note,
                    }
                )

            # Section 3: shadow-DOM census (from the derived-prefix census).
            derived_census = candidates[0].census
            shadow_report = {
                "open_root_count": derived_census["open_shadow_root_count"],
                "piercing_recovery_delta": derived_census["piercing_recovery"],
            }

            # Section 4: pagination dry-run.
            pagination_report = await _probe_pagination(page)

            # Section 5: filter census.
            filters_report = await _probe_filters(page)

            # Section 6: platform fingerprints + network APIs.
            dom_hits = await _probe_dom_markers(page)
            rendered_host = urlparse(rendered_url).hostname or ""
            fingerprint_hits = evaluate_fingerprints(
                rendered_host, dom_hits, network_urls
            )
            unclassified_apis = find_unclassified_job_apis(
                json_response_urls, fingerprint_hits
            )

            # Section 7: suggestion assembly.
            derived_matcher_count = candidates[0].matcher_count
            derived_eligible = derived_census["eligible_incl_hidden"]
            hidden_heavy = (
                derived_eligible >= 5
                and derived_census["hidden_count"] >= derived_eligible / 2
            )
            zero_anchor_with_api = derived_matcher_count == 0 and (
                any(h.signals for h in fingerprint_hits) or bool(unclassified_apis)
            )
            filter_findings = _summarise_filter_findings(filters_report)

            suggestion = build_suggestion(
                company_snippet_name="<Company Name>",
                job_board_url=job_board_url,
                sample_job_url=sample_job_url,
                derived_prefix=derived_prefix,
                min_depth_suggestion=promoted_min_depth,
                min_depth_prefix=promoted_min_depth_prefix,
                fingerprint_hits=fingerprint_hits,
                paginate_found=bool(pagination_report["found"]),
                hidden_heavy=hidden_heavy,
                zero_anchor_with_api=zero_anchor_with_api,
                cross_origin_frame_with_anchors=cross_origin_with_anchors,
                filter_findings_summary=filter_findings,
            )

            return _assemble_report(
                job_board_url=job_board_url,
                sample_job_url=sample_job_url,
                rendered_url=rendered_url,
                rendered_at=rendered_at,
                candidates=candidates,
                frames=frames_report,
                shadow=shadow_report,
                pagination=pagination_report,
                filters=filters_report,
                fingerprint_hits=fingerprint_hits,
                unclassified_apis=unclassified_apis,
                suggestion=suggestion,
            )
        finally:
            await browser.close()


def _summarise_filter_findings(filters_report: dict[str, Any]) -> str | None:
    """One-line human summary of the filter census for the notes list.

    Returns ``None`` when no location-shaped controls were found — the
    absence itself is a datapoint, but "surfaced: nothing" would read
    oddly and only clutters the notes column.
    """
    parts: list[str] = []
    for key, label in (
        ("selects", "native <select>"),
        ("comboboxes", "ARIA combobox"),
        ("checkbox_groups", "checkbox facet"),
        ("search_boxes", "search box"),
    ):
        count = len(filters_report.get(key) or [])
        if count > 0:
            parts.append(f"{count} {label}(s)")
    if not parts:
        return None
    return ", ".join(parts)


def _assemble_report(
    *,
    job_board_url: str,
    sample_job_url: str,
    rendered_url: str,
    rendered_at: str,
    candidates: list[_CandidateSection],
    frames: list[dict[str, Any]],
    shadow: dict[str, Any],
    pagination: dict[str, Any],
    filters: dict[str, Any],
    fingerprint_hits: list[FingerprintHit],
    unclassified_apis: list[str],
    suggestion: dict[str, Any],
) -> dict[str, Any]:
    """Compose the final report dict shared by the text renderer and JSON mode."""
    prefix_candidates: list[dict[str, Any]] = []
    for c in candidates:
        cen = c.census
        # depth_histogram is normalised to int-keyed at census-read time;
        # dump keys back to strings here so ``json.dumps`` is stable
        # regardless of dict insertion order across Python versions.
        depth_hist_dumpable = {
            str(k): v for k, v in sorted(cen["depth_histogram"].items())
        }
        prefix_candidates.append(
            {
                "prefix": c.prefix,
                "label": c.label,
                "matcher_count": c.matcher_count,
                "eligible_incl_hidden": cen["eligible_incl_hidden"],
                "hidden_count": cen["hidden_count"],
                "fragment_self_links": cen["fragment_self_links"],
                "fragment_variants": cen["fragment_variants"],
                "depth_histogram": depth_hist_dumpable,
                "min_depth_suggestion": c.min_depth_suggestion,
            }
        )

    fingerprints_dumpable = [
        {
            "platform": h.platform,
            "signals": list(h.signals),
            "strategy_suggestion": h.strategy_suggestion,
        }
        for h in fingerprint_hits
    ]

    return {
        "job_board_url": job_board_url,
        "sample_job_url": sample_job_url,
        "rendered_url": rendered_url,
        "rendered_at": rendered_at,
        "standing_caveat": (
            "This probe renders the board once and drives nothing. Filters "
            "are inventoried, not applied; accordions are not expanded. "
            "C3/C4 boards surface as filter-census findings, not "
            "post-filter counts."
        ),
        "prefix_candidates": prefix_candidates,
        "frames": frames,
        "shadow": shadow,
        "pagination": pagination,
        "filters": filters,
        "fingerprints": fingerprints_dumpable,
        "network_apis": {
            "unclassified_job_api_candidates": unclassified_apis,
        },
        "suggestion": suggestion,
    }


# ---------------------------------------------------------------------------
# Text renderer
# ---------------------------------------------------------------------------


def render_text_report(report: dict[str, Any]) -> str:
    """Render the report dict as the human-readable console output.

    Kept a pure function of the report dict so ``--json`` mode and text
    mode share exactly the same data, and so the renderer is trivial to
    unit-test.
    """
    out = io.StringIO()
    write = out.write

    write("BOARD PROFILE\n")
    write("=============\n")
    write(f"url:            {report['job_board_url']}\n")
    write(f"sample_job_url: {report['sample_job_url']}\n")
    rendered_url = report["rendered_url"]
    redirected = rendered_url != report["job_board_url"]
    write(
        f"rendered_url:   {rendered_url}"
        f"   (redirected: {'yes' if redirected else 'no'})\n"
    )
    write(f"rendered_at:    {report['rendered_at']}\n")
    write("\n")
    write("Standing caveat:\n")
    write(f"  {report['standing_caveat']}\n")
    write("\n")

    # Section 1
    write("1. ANCHOR CENSUS PER CANDIDATE PREFIX\n")
    write("-------------------------------------\n")
    for cand in report["prefix_candidates"]:
        write(f"  Candidate: {cand['prefix']}  ({cand['label']})\n")
        write(f"    matcher count (EXTRACT_JOB_LINKS_JS):  {cand['matcher_count']}\n")
        write(
            f"    eligible incl hidden (probe diagnostic): "
            f"{cand['eligible_incl_hidden']}\n"
        )
        write(f"    hidden count:            {cand['hidden_count']} (C8 signal)\n")
        write(f"    fragment self-links:     {cand['fragment_self_links']}\n")
        write(f"    fragment variants (dedup): {cand['fragment_variants']}\n")
        hist = cand["depth_histogram"]
        if hist:
            hist_str = ", ".join(f"depth {k}: {v}" for k, v in hist.items())
        else:
            hist_str = "(no id-in-path anchors)"
        write(f"    depth histogram:         {hist_str}\n")
        if cand["min_depth_suggestion"] is not None:
            write(
                f"    min_depth suggestion:    "
                f"{cand['min_depth_suggestion']} (C9 signal)\n"
            )
        write("\n")

    # Section 2
    write("2. FRAME MAP\n")
    write("------------\n")
    if not report["frames"]:
        write("  (no child frames)\n")
    else:
        for f in report["frames"]:
            reach = "yes" if f["runtime_reachable"] else "no (cross-origin)"
            cnt = f["census_count"]
            cnt_str = "n/a" if cnt is None else str(cnt)
            write(
                f"  Frame [{f['index']}]: {f['url']}\n"
                f"    origin: {f['origin']}  runtime-reachable: {reach}\n"
                f"    census (matcher-visible under derived prefix): {cnt_str}\n"
            )
            if f["note"]:
                write(f"    note: {f['note']}\n")
            # Placeholder frames (about:blank, data:, javascript:) are
            # driver-side artefacts and not a real C5 signal — suppress
            # the "cross-origin frame contains anchors" annotation on
            # them. See build_suggestion's cross_origin_frame_with_anchors
            # gate for the corresponding change on the suggestion side.
            origin_scheme = (f["origin"] or "").split("://", 1)[0]
            is_placeholder = origin_scheme in {"about", "data", "javascript", ""}
            if not f["runtime_reachable"] and not is_placeholder and cnt and cnt > 0:
                write(
                    "    ** C5 signal: cross-origin frame contains "
                    "anchors the in-page matcher cannot reach **\n"
                )
    write("\n")

    # Section 3
    write("3. SHADOW DOM\n")
    write("-------------\n")
    sh = report["shadow"]
    write(f"  open shadow roots:        {sh['open_root_count']}\n")
    delta = sh["piercing_recovery_delta"]
    tag = " (C6 signal)" if delta > 0 else ""
    write(f"  piercing recovery delta:  {delta}{tag}\n")
    write("\n")

    # Section 4
    write("4. PAGINATION AFFORDANCES\n")
    write("-------------------------\n")
    pg = report["pagination"]
    if pg["found"]:
        write("  found:        yes  (C7 signal)\n")
        write(f"  signal:       {pg['signal']}\n")
        write(f"  control text: {pg['control_text']!r}\n")
    else:
        write("  found:        no\n")
    write("\n")

    # Section 5
    write("5. FILTER-CONTROL CENSUS\n")
    write("------------------------\n")
    fl = report["filters"]

    def _print_bucket(key: str, label: str) -> None:
        entries = fl.get(key) or []
        write(f"  {label}: {len(entries)}\n")
        for e in entries:
            ident = e.get("name") or e.get("id") or e.get("label") or "(unnamed)"
            write(f"    - {e['tag']} {ident!r}\n")
            if e.get("sample_options"):
                opts = ", ".join(repr(o) for o in e["sample_options"][:5])
                write(f"      sample options: {opts}\n")

    _print_bucket("selects", "Native <select> with location tokens (C3 signal)")
    _print_bucket("comboboxes", "ARIA comboboxes")
    _print_bucket("checkbox_groups", "Checkbox facet groups (C2/C3 signal)")
    _print_bucket("search_boxes", "Bare search boxes (C2 signal)")
    write("\n")

    # Section 6
    write("6. PLATFORM FINGERPRINTS\n")
    write("------------------------\n")
    if report["fingerprints"]:
        for fp in report["fingerprints"]:
            signals = ", ".join(fp["signals"])
            write(f"  - {fp['platform']}  (signals: {signals})\n")
    else:
        write("  (no platform fingerprints matched)\n")
    apis = report["network_apis"]["unclassified_job_api_candidates"]
    if apis:
        write("\n  Unclassified job-API candidates (G4 signal):\n")
        for url in apis[:10]:
            write(f"    - {url}\n")
        if len(apis) > 10:
            write(f"    ... and {len(apis) - 10} more\n")
    write("\n")

    # Section 7
    write("7. SUGGESTED COMPANY ENTRY\n")
    write("--------------------------\n")
    sug = report["suggestion"]
    for line in sug["company_snippet"].splitlines():
        write(f"  {line}\n")
    write("\n")
    if sug["notes"]:
        write("  Notes:\n")
        for note in sug["notes"]:
            write(f"    - {note}\n")
    write("\n")
    write("  Caveats:\n")
    for caveat in sug["caveats"]:
        write(f"    - {caveat}\n")
    write("\n")

    return out.getvalue()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Board profiler: render a career site once and emit a structured "
            "diagnostic report screening for every INTEGRATION_BLOCKERS class."
        ),
    )
    parser.add_argument(
        "-u",
        "--job-board-url",
        required=True,
        help="The listing page URL the probe should render.",
    )
    parser.add_argument(
        "-s",
        "--sample-job-url",
        required=True,
        help=(
            "An example individual job URL. The derived path prefix and its "
            "ancestors define the per-candidate census in section 1."
        ),
    )
    parser.add_argument(
        "--wait",
        type=int,
        default=DEFAULT_WAIT_S,
        help=(
            f"Seconds to wait after navigation for JS rendering "
            f"(default {DEFAULT_WAIT_S})."
        ),
    )
    parser.add_argument(
        "--scroll",
        type=int,
        default=DEFAULT_SCROLL_N,
        help=(
            f"Viewport-height scrolls to perform after the wait "
            f"(default {DEFAULT_SCROLL_N})."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help=(
            "Emit the report as a JSON document on stdout (same data as "
            "the text renderer). Suitable for the integrate-company skill "
            "to consume programmatically."
        ),
    )
    return parser.parse_args(argv)


def main() -> None:
    args = _parse_args()
    try:
        report = asyncio.run(
            _probe(
                args.job_board_url,
                args.sample_job_url,
                args.wait,
                args.scroll,
            )
        )
    except KeyboardInterrupt:
        sys.exit(130)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        sys.stdout.write(render_text_report(report))


if __name__ == "__main__":
    main()
