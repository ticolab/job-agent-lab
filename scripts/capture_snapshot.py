#!/usr/bin/env python3
"""Capture a frozen rendered-HTML snapshot of a company's listing page.

Used by the snapshot regression harness described in ``ARCHITECTURE.md``.
Each invocation renders ``company.job_board_url`` in headless Chromium
(with the same launch arguments the production agent uses), runs the
production matcher as a sanity check, and writes ``page.html`` plus
``metadata.json`` into ``tests/fixtures/snapshots/<slug>/``. The regression
test suite picks the new fixture up automatically on next run.

Snapshots represent the **unfiltered** listing-page state. The
``--expected`` flag locks in the human-verified unfiltered count for that
snapshot. The location-filtered integration target (e.g. Costa Rica-only)
remains the concern of the live end-to-end run, not the snapshot test.

Run from the repo root:

    uv run python scripts/capture_snapshot.py -c <handle> --expected <N>

The handle resolves with the same rules as the main CLI (alias, acronym,
or name substring). Pass ``--force`` to overwrite an existing snapshot
without prompting.

Schema v2 (SYS-2)
-----------------
The capture now bakes CSS visibility state into the serialized HTML
(anchors that fail the matcher's visibility gate get an inline
``display: none`` stamp) and emits open shadow roots as declarative
``<template shadowrootmode="open">`` fragments via
``Element.getHTML({serializableShadowRoots: true, shadowRoots: [...]})``.
Same-origin frames reachable through a same-origin ancestor chain and
containing ≥1 anchor are serialized the same way into
``frames/<index>-<name>.html`` beside ``page.html``, and each entry is
recorded in ``metadata.frames`` as ``{file, url}`` so the harness can
replay them under the correct base href. Simple boards (no captured
frames) omit the ``frames`` key entirely for byte-stable metadata.

Pagination (SYS-5)
------------------
Passing ``--paginate`` drives the same
:func:`~vacantes.extraction.dom.collector.walk_and_collect` loop
the runtime uses when ``Company.paginate=True``, over a Playwright
``PageDriver`` defined below. After baking state 1 as ``page.html``
(with its usual ``frames/`` sidecar), each subsequent DOM state is
baked and written as ``pages/page-N.html`` (N ≥ 2) with a conditional
``metadata.pages: [{file, url}]`` list mirroring the ``frames``
convention. The harness unions state 1 + frames + pages just like it
unions state 1 + frames today. Frames are captured for state 1 only
— a documented limitation, matching the boards SYS-5 targets
(Techwarely and BCG both have their frames-if-any at state 1). Boards
run without ``--paginate`` produce the exact SYS-2 fixture shape with
no ``pages`` key emitted.

Expand-on-capture (SYS-6, SYS-12)
---------------------------------
Passing ``--expand-selector <css>`` clicks every visible element
matching the selector after the render+scroll settle and before the
bake+serialize pass, in up to :data:`EXPAND_MAX_ROUNDS` rounds with a
:data:`EXPAND_SETTLE_SEC`-second sleep between rounds. The loop stops
as soon as a round finds no visible matches. This exists so boards
whose listings sit behind collapsed department accordions (Deel is
the canonical C4 case) can be captured with their anchors already
mounted in the frozen HTML — the runtime agent handles the same job
in-page under the SYS-6 prompt clause, and the fixture must reflect
the same DOM state to keep the harness honest. The user-supplied
selector is recorded as ``metadata.expand_selector`` (conditional key
— omitted when the flag is not set, byte-stable with prior fixtures).
The flag is mutually exclusive with ``--paginate`` at the argparse
layer: no board in the current corpus needs both, and the interaction
is untested. Runtime expansion of accordions in production is the
agent's job under GOAL_PROMPT §STEP 2, not the collector's — this
flag is capture-side only.

Under SYS-12 the bounded expansion loop has been extracted into
:func:`~vacantes.extraction.dom.collector.expand_all` — one
production loop shared between runtime (driven by ``ActorPageDriver``)
and capture (driven by :class:`_PlaywrightPageDriver`). Its cap
constant and settle sleep now live on the collector module and are
imported here so both surfaces agree by construction. The
``--expand-selector`` flag also gains override semantics against a
catalog-configured ``Company.hooks.expand_selector``: when both are
present the flag wins, so an integrator iterating on a new selector
via the probe/GT workflow doesn't have to re-edit the catalog on
every attempt. When only the catalog value is set the flag can be
omitted and the same effective selector still runs.

Runtime hooks (SYS-12)
----------------------
Beyond the SYS-6 ``--expand-selector`` override, capture also honours
the other two DOM-side fields on :class:`RuntimeHooks`:
``pre_extract_css`` and ``next_control_selector``. Both are read
directly from ``Company.hooks`` — no CLI flag — and threaded into the
same execution order used at runtime (§4.5 of
``ARCHITECTURE_PROPOSAL_R2.md``): CSS injection first, then bounded
expansion, then the bake+serialize pass, then per-frame captures,
then the optional pagination walker with the next-control selector as
a per-state override. All three hook values, when non-empty, are
recorded verbatim as conditional metadata keys (``pre_extract_css``,
``expand_selector``, ``next_control_selector``) so a re-capture
producing byte-different HTML can be traced back to the exact hook
payload. Fixtures for companies with the inert-default hook stay
byte-stable with the pre-SYS-12 schema.

Pre-filter URLs (SYS-13)
------------------------
Boards whose location filter is expressible as a stable set of URL
variants declare them on ``Company.pre_filter_urls`` (a tuple of
same-origin URLs). Capture reads that tuple directly — no CLI flag —
and switches into agent-less multi-state mode: state 1 renders from
``pre_filter_urls[0]`` (not ``job_board_url``, which the runtime
never visits on this path), then each subsequent
``pre_filter_urls[N-1]`` is fetched, settled, hooks 1–2 re-applied,
and baked as ``states/state-N.html``. The reported extractor count
is the union across every state's matcher run — the same union
:func:`~vacantes.extraction.dom.strategy.DomStrategy._extract_prefiltered`
computes at runtime — so the ``--expected`` sanity check aligns with
the harness assertion. Three additive-optional metadata keys are
emitted together on declaring captures (``pre_filter_urls`` verbatim,
``top_url`` = state 1 URL, ``states`` = list of ``{file, url}``);
non-declaring fixtures skip all three and stay byte-identical to
their pre-SYS-13 shape. Frames are walked on state 1 only (SYS-5
limitation carried forward).

Declaring × paginate
--------------------
A declaring board whose per-state listing is itself paged combines the
two axes — Accenture is the first: one ``pre_filter_urls`` state, five
pages behind a ``Next`` button that never changes the URL, so the pages
cannot be decomposed into further pre-filter URLs. ``--paginate`` on a
declaring capture drives :func:`walk_and_collect` *within* each state,
exactly as the runtime's ``_extract_prefiltered`` does when
``Company.paginate=True``. State 1's pages land in the existing
top-level ``pages/page-M.html`` (that key already means "pagination
states of the top document"); each state N ≥ 2 gets its own
``states/state-N-pages/page-M.html`` sidecar, recorded as a nested
``pages`` list on the state entry — the sibling-directory convention
``state-N-frames/`` established. Both keys stay additive-optional, so
every fixture without the combination replays byte-identically.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import Frame, Page, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from vacantes.catalog import find_company, slugify
from vacantes.domain.company import Company, RuntimeHooks
from vacantes.extraction.dom import EXTRACT_JOB_LINKS_JS
from vacantes.extraction.dom.collector import (
    apply_pre_extract_css,
    expand_all,
    walk_and_collect,
)
from vacantes.extraction.dom.rules import derive_path_prefix
from vacantes.settings import (
    RENDER_SCROLL_COUNT,
    RENDER_WAIT_SEC,
    plausible_headless_ua,
)

SNAPSHOT_SCHEMA_VERSION = 2
SNAPSHOTS_DIR = (
    Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "snapshots"
)

# The bounded expansion loop lives in
# :func:`~vacantes.extraction.dom.collector.expand_all` as of
# SYS-12 — one production loop, two adapters (runtime uses
# ``ActorPageDriver``; capture uses ``_PlaywrightPageDriver`` below).
# The round cap and settle-sleep constants live on the collector
# module (``EXPAND_MAX_ROUNDS`` and ``EXPAND_SETTLE_SEC``) so the two
# surfaces are guaranteed to agree; this script no longer owns them
# and does not need to import them at module scope.

# In-page bake + serialize routine, run once against the top document and
# once against each captured frame. Behaviour:
#   1. Recursively walks the target document plus every reachable open
#      shadow root plus every same-origin iframe/frame contentDocument
#      (same traversal shape as the matcher, in the same execution context).
#      Anchors that fail the standards-track visibility check
#      (``checkVisibility({checkVisibilityCSS: true})``) get an inline
#      ``display: none`` stamp so the JS-disabled test harness can decide
#      visibility from the frozen bytes alone.
#   2. Collects every reachable open shadow root and hands the list to
#      ``documentElement.getHTML({serializableShadowRoots: true,
#      shadowRoots})`` so declarative shadow DOM survives serialization
#      (``page.content()`` drops imperative shadow trees entirely; this
#      replaces that call). The output is prefixed with ``<!DOCTYPE html>``
#      so the framing matches ``page.content()``'s.
#   3. Also counts anchors so the caller can filter out frames that would
#      contribute nothing to the union.
# Note: the bake stamps only anchors, not their subtrees — anchors are all
# the matcher interrogates, and subtree-level baking would wrongly hide
# ``visibility: visible`` descendants of a ``visibility: hidden`` container.
_BAKE_AND_SERIALIZE_JS = r"""
() => {
    const shadowRoots = [];
    const seenRoots = new WeakSet();
    let anchorCount = 0;

    const isVisible = (el) => {
        if (typeof el.checkVisibility === 'function') {
            return el.checkVisibility({ checkVisibilityCSS: true });
        }
        if (el.offsetParent !== null) return true;
        try { return getComputedStyle(el).position === 'fixed'; }
        catch (e) { return false; }
    };

    const walk = (root) => {
        if (!root || seenRoots.has(root)) return;
        seenRoots.add(root);
        let elements;
        try { elements = root.querySelectorAll('*'); }
        catch (e) { return; }
        for (const el of elements) {
            if (el.tagName === 'A') {
                anchorCount++;
                if (!isVisible(el)) {
                    // Inline !important beats any external stylesheet the
                    // snapshot would otherwise re-apply on replay.
                    el.style.setProperty('display', 'none', 'important');
                }
            }
            if (el.shadowRoot) {
                shadowRoots.push(el.shadowRoot);
                walk(el.shadowRoot);
            }
            if (el.tagName === 'IFRAME' || el.tagName === 'FRAME') {
                let doc;
                try { doc = el.contentDocument; }
                catch (e) { continue; }
                if (doc) walk(doc);
            }
        }
    };
    walk(document);

    let html;
    if (typeof document.documentElement.getHTML === 'function') {
        // Empirical per-root validity probe. The walk above collects
        // every el.shadowRoot it encounters, but on aggressively
        // re-rendering platforms (notably Salesforce Lightning Web
        // Components on Experience Cloud tenants) the array can
        // accumulate entries that are Proxies of native ShadowRoots
        // rather than native ShadowRoots themselves. Proxies are
        // transparent by JavaScript design: `r instanceof ShadowRoot`,
        // duck-typed property access on `host`/`mode`/etc, and
        // `r.constructor.name` all pass through the Proxy handler to
        // the real target, so no upstream JS-level filter can
        // discriminate. The Chromium native binding layer that
        // implements Element.getHTML rejects the Proxy at the C++
        // ShadowRoot conversion boundary with the misleading error
        // "Failed to convert value to 'ShadowRoot'". The only reliable
        // filter is to invoke getHTML with a single-element array per
        // walked root; entries that raise are Proxied or otherwise
        // non-convertible and are dropped, entries that pass are
        // guaranteed convertible in the aggregate call. Probe scope is
        // document.body (falling back to documentElement) which is
        // cheaper than serializing the full documentElement tree per
        // iteration. On boards with well-behaved shadow trees every
        // probe succeeds and the emitted HTML is byte-identical to the
        // pre-filter output.
        const probeScope = document.body || document.documentElement;
        const goodShadowRoots = [];
        for (const r of shadowRoots) {
            try {
                probeScope.getHTML({
                    serializableShadowRoots: true,
                    shadowRoots: [r],
                });
                goodShadowRoots.push(r);
            } catch (e) {
                // Non-convertible entry — skip.
            }
        }
        html = document.documentElement.getHTML({
            serializableShadowRoots: true,
            shadowRoots: goodShadowRoots,
        });
    } else {
        // Older Chromium fallback: no declarative-shadow-DOM emission.
        html = document.documentElement.outerHTML;
    }
    // ``getHTML`` returns the outer ``<html>...</html>``; prepend the
    // doctype so the framing matches Playwright's ``page.content()``
    // output. Attributes on ``<html>`` (e.g. ``lang``) are preserved.
    return { html: '<!DOCTYPE html>\n' + html, anchorCount };
}
"""

# Per-click budget for the capture driver's first-attempt mouse click.
# Deliberately short: a control that has not become actionable within
# this window is one the runtime would click via JS anyway, so waiting
# out Playwright's 30s default just delays the fallback.
_CLICK_TIMEOUT_MS = 5000

# Filesystem-safe name for a captured frame file. Empty / all-symbol names
# fall back to ``frame`` so the ``<index>-<name>.html`` shape is preserved.
_FRAME_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _sanitize_frame_name(name: str) -> str:
    cleaned = _FRAME_NAME_RE.sub("-", name).strip("-")
    return (cleaned or "frame")[:40]


async def _same_origin_ancestor_chain(frame: Frame, top_origin: str) -> bool:
    """Return True iff every frame from ``frame`` up to (but not including)
    the main frame is same-origin with ``top_origin``.

    Playwright's ``page.frames`` is a flat list; a same-origin frame nested
    under a cross-origin frame is driver-visible but runtime-unreachable
    (the in-page walk stops at the cross-origin boundary). Enumerating only
    frames with a same-origin ancestor chain keeps capture and runtime
    consistent — the harness never sees frames the matcher couldn't reach.
    """
    node: Frame | None = frame
    while node is not None and node.parent_frame is not None:
        parsed = urlparse(node.url)
        node_origin = f"{parsed.scheme}://{parsed.netloc}"
        if node_origin != top_origin:
            return False
        node = node.parent_frame
    return True


async def _capture_same_origin_frames(
    page: Page, top_origin: str
) -> list[tuple[str, str, str]]:
    """Bake every same-origin descendant frame of ``page`` that holds anchors.

    Returns ``(name, url, html)`` triples ready for the metadata writer.
    Frames whose ancestor chain leaves ``top_origin``, frames that fail
    to serialize, and frames with zero anchors are skipped — an anchorless
    frame is chrome (a tracking pixel, a video embed) and freezing it would
    only inflate the fixture.

    Called once for state 1 and once per SYS-13 state >= 2. Per-state frames
    were a documented non-goal through SYS-13 because no corpus board
    combined same-origin frame descent with ``pre_filter_urls``: every
    frame-bearing fixture was single-state, and every multi-state fixture
    kept its anchors in the top document. Auxis is the first board where
    both hold at once — an iCIMS portal whose entire listing lives inside
    ``#icims_content_iframe``, paged by a URL cursor the top-document pager
    walk cannot see. Freezing only state 1's frame left states >= 2 as
    anchorless shells, so the fixture replayed 10 of 11 links while the
    live runtime returned all 11. The asymmetry was in the capture side
    alone: the matcher asset already descends same-origin frames in-page
    at every state.
    """
    frames: list[tuple[str, str, str]] = []
    for idx, frame in enumerate(page.frames):
        if frame is page.main_frame:
            continue
        if not await _same_origin_ancestor_chain(frame, top_origin):
            continue
        try:
            frame_html, anchor_count = await _bake_and_serialize(frame)
        except Exception:  # noqa: BLE001 — best-effort per-frame
            continue
        if anchor_count == 0:
            continue
        name = _sanitize_frame_name(frame.name)
        frames.append((f"{idx}-{name}", frame.url, frame_html))
    return frames


async def _bake_and_serialize(scope: Page | Frame) -> tuple[str, int]:
    """Run the bake+serialize routine in ``scope``'s execution context.

    Works with either the top ``Page`` (bakes the main frame) or a
    ``Frame`` (bakes that subframe's document). Returns
    ``(html, anchor_count)``.
    """
    result = await scope.evaluate(_BAKE_AND_SERIALIZE_JS)
    return result["html"], int(result["anchorCount"])


def resolve_expand_selector(
    hooks: RuntimeHooks, flag_override: str | None
) -> str | None:
    """Return the effective ``expand_selector`` for a capture run.

    Precedence: the ``--expand-selector`` CLI flag wins whenever it is
    provided (even against a catalog-configured
    ``RuntimeHooks.expand_selector``); otherwise the catalog value is
    used; otherwise ``None`` (no expansion). A pure function so it can
    be unit-tested without touching Playwright or the filesystem.

    The asymmetry — flag beats catalog — is deliberate: an integrator
    who has just discovered a new accordion shape via the probe/GT
    workflow needs to iterate on the selector without re-editing the
    catalog on every attempt. Once the selector is settled it moves
    into ``Company.hooks`` and the flag is dropped from the invocation.
    The two are never additive (no union or fallback chain); the flag
    is a full override of the catalog value.
    """
    return flag_override if flag_override is not None else hooks.expand_selector


class _PlaywrightPageDriver:
    """Adapter satisfying :class:`PageDriver` over a Playwright ``Page``.

    Kept local to the capture script rather than promoted to the
    ``collector`` module because Playwright is a scripts-side dependency
    and the runtime uses browser-use's page handle (served by
    :class:`~vacantes.extraction.dom.collector.ActorPageDriver`).
    The two adapters share the same three-method protocol so
    :func:`~vacantes.extraction.dom.collector.walk_and_collect` is
    driven identically from both surfaces — one production loop, two
    minimal adapters.
    """

    def __init__(self, page: Page) -> None:
        self._page = page

    async def evaluate(self, js: str, arg: Any) -> Any:
        # ``page.evaluate`` in the async API returns the JS value
        # already parsed, so no JSON decode is needed here (in
        # contrast to :class:`ActorPageDriver`).
        return await self._page.evaluate(js, arg)

    async def click(self, selector: str) -> None:
        # ``page.click`` performs a CDP mouse-event click *plus*
        # actionability + auto-wait for any navigation the click
        # triggers, which is what anchor-based next controls want, so
        # it stays the first attempt and every previously-captured
        # board keeps byte-identical semantics.
        #
        # It is not, however, universally equivalent to the runtime's
        # click. ActorPageDriver fires ``HTMLElement.click()`` via
        # ``evaluate``, which has no actionability gate. Nextern's
        # Workable board is the counter-example that retired the
        # earlier "the observable click contract is the same either
        # way" claim: its ``button[data-ui="load-more-button"]`` is
        # present and hit-testable enough for the runtime, but never
        # satisfies Playwright's actionability wait, so ``page.click``
        # raises ``TimeoutError`` while the JS click advances the list
        # from 10 anchors to 19. ``walk_and_collect`` treats a failed
        # click as "no further states", so the capture silently froze
        # page 1 and recorded an under-count instead of failing loudly.
        #
        # Falling back to the runtime's own mechanism keeps capture
        # faithful to runtime — the entire premise of the snapshot
        # corpus — without giving up the navigation auto-wait on the
        # boards that rely on it.
        try:
            await self._page.click(selector, timeout=_CLICK_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            await self._page.evaluate(
                "(sel) => { const el = document.querySelector(sel);"
                " if (el) el.click(); }",
                selector,
            )

    async def url(self) -> str:
        result: str = self._page.url
        return result


async def _capture(
    job_board_url: str,
    sample_job_url: str,
    wait_s: int,
    scroll_n: int,
    *,
    path_prefix: str | None = None,
    min_depth: int = 1,
    suppress_selector: str | None = None,
    paginate: bool = False,
    pre_extract_css: str | None = None,
    expand_selector: str | None = None,
    next_control_selector: str | None = None,
    pre_filter_urls: tuple[str, ...] = (),
) -> tuple[
    str,
    list[tuple[str, str, str]],
    list[tuple[int, str, str]],
    list[tuple[int, str, str, list[tuple[str, str, str]], list[tuple[int, str, str]]]],
    int,
]:
    """Render, bake, and serialize; return top HTML, frames, pages, states, and count.

    ``frames`` is a list of ``(sanitized_name, url, html)`` tuples, one
    per same-origin-ancestor-chain frame that contains ≥1 anchor, for the
    initial (state 1) DOM. On the ``pre_filter_urls`` path each state
    ≥ 2 carries its own such list as the 4th element of its ``states``
    tuple, and its own pagination pages ``(page_index, url, html)`` as
    the 5th (empty unless ``paginate`` is also on); on the ``paginate``
    path frames remain state-1-only, matching the boards the walker
    targets.

    ``pages`` is a list of ``(state_index, url, html)`` tuples, empty
    unless ``paginate`` is True. When paginate is on the walker drives
    the same :func:`walk_and_collect` loop the runtime uses; state 1's
    top HTML is already baked before the walker starts, and each state
    N ≥ 2 is baked in an ``on_state`` callback that fires after the
    walker's per-state matcher run. State 1's URL is
    ``metadata.job_board_url`` (already recorded), so state 1 is not
    included in the returned ``pages`` list.

    The extractor count is the same one the harness will assert
    against: when paginate is off, the matcher's post-bake count on
    state 1; when paginate is on, the union size across every state
    the walker successfully collected.

    ``path_prefix`` mirrors the ``Company.link_rule.path_prefix``
    override; ``min_depth`` mirrors ``Company.link_rule.min_depth``.
    ``paginate`` mirrors ``Company.paginate`` — passed in via
    ``--paginate`` rather than derived from the catalog entry so
    scratch capture with the flag toggled off (for a paginate=True
    Company) still produces a state-1-only fixture on demand.

    ``pre_extract_css``, ``expand_selector``, and
    ``next_control_selector`` (SYS-12) mirror the fields on
    :class:`RuntimeHooks`. ``expand_selector`` is the *resolved*
    effective value (see :func:`resolve_expand_selector` — flag
    override beats catalog); the other two are catalog-sourced only
    (no capture-side flag). Execution order per §4.5 of
    ``ARCHITECTURE_PROPOSAL_R2.md``: render → wait → scroll →
    ``apply_pre_extract_css`` → ``expand_all`` → bake → frames →
    optional walker (threading ``next_control_selector``). CSS
    injection precedes the bake so the visibility stamps and the
    serialized ``<style data-jal-css>`` both reflect the unhidden
    state — the harness never runs the injection JS on replay, but
    the bake's ``display:none`` stamps on invisible anchors and the
    style element carried in the frozen ``<head>`` together preserve
    the same visibility gate the runtime enforces. Expansion runs
    against state 1 only (documented limitation, mirroring
    frames-on-state-1). The catalog ``paginate`` ⟷ hook combination
    is legal — the argparse ``--paginate`` ⟷ ``--expand-selector``
    mutex is a *flag*-level constraint.

    ``pre_filter_urls`` (SYS-13) mirrors ``Company.pre_filter_urls``:
    an empty tuple (the pre-SYS-13 default and every non-declaring
    board) leaves navigation byte-identical — state 1 renders from
    ``job_board_url`` and ``states`` returns empty. A non-empty tuple
    switches capture into agent-less multi-state mode: state 1
    renders from ``pre_filter_urls[0]`` (not ``job_board_url`` — the
    runtime never visits the board root on this path, so the frozen
    ``page.html`` must reflect the state 1 URL to keep the harness
    honest), then each subsequent ``pre_filter_urls[N-1]`` is
    fetched, settled, and baked as ``states/state-N.html``.
    Per-state execution order matches state 1: goto → wait → scroll
    → ``apply_pre_extract_css`` → ``expand_all`` → bake. Frames are
    walked on state 1 only (SYS-5 limitation carried forward). When
    ``pre_filter_urls`` is non-empty the reported extractor count is
    the union across every state's matcher run — the same union
    :func:`_extract_prefiltered` computes at runtime — so the
    ``--expected`` sanity check aligns with the harness assertion.
    With ``paginate`` also on, the walker runs *within* each state —
    state 1's pages go to ``pages``, state N's to the 5th element of
    its ``states`` tuple — mirroring ``_extract_prefiltered``, which
    forwards ``Company.paginate`` into every per-state
    ``collect_job_links`` call.
    """
    parsed = urlparse(job_board_url)
    top_origin = f"{parsed.scheme}://{parsed.netloc}"
    prefix = (
        path_prefix if path_prefix is not None else derive_path_prefix(sample_job_url)
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--password-store=basic", "--use-mock-keychain"],
        )
        try:
            # SYS-10: all launch sites must agree — a board is validated
            # and run under one browser environment. The plausible UA
            # (``HeadlessChrome/<v>`` → ``Chrome/<v>``) closes the C14
            # WAF-403 class documented in
            # ``blockers/INTEGRATION_BLOCKERS_R2.md``.
            user_agent = await plausible_headless_ua()
            page = await browser.new_page(user_agent=user_agent)
            # SYS-13: when ``pre_filter_urls`` is non-empty the runtime
            # never visits ``job_board_url``, so state 1 must render
            # from ``pre_filter_urls[0]`` to keep the frozen fixture in
            # sync with what the harness will replay. Empty tuple keeps
            # the pre-SYS-13 code path byte-identical.
            state_1_url = pre_filter_urls[0] if pre_filter_urls else job_board_url
            await page.goto(state_1_url)
            await asyncio.sleep(wait_s)
            for _ in range(scroll_n):
                await page.evaluate("() => { window.scrollBy(0, window.innerHeight); }")
                await asyncio.sleep(1)

            # SYS-12 §4.5 execution order: css → expand → bake →
            # frames → walker. The Playwright adapter is constructed
            # once here and reused across every hook that needs it,
            # mirroring the runtime's single-``ActorPageDriver``
            # convention inside ``collect_job_links``.
            driver = _PlaywrightPageDriver(page)

            # SYS-12 pre-extract CSS injection. Runs *before* expansion
            # so any stylesheet-hidden accordion trigger becomes
            # visibility-gate-visible in time for ``expand_all`` to
            # click it, and *before* the bake so the injected
            # ``<style data-jal-css>`` element is serialized into the
            # frozen ``<head>`` and the anchor visibility stamps
            # reflect the unhidden state.
            if pre_extract_css is not None:
                rule_count = await apply_pre_extract_css(driver, pre_extract_css)
                print(f"   pre-extract-css: {rule_count} rule(s) parsed")

            # SYS-6/SYS-12: expand accordions before the bake so their
            # anchors are captured in the frozen HTML. The bounded
            # loop lives in ``collector.expand_all`` — one production
            # loop, driven here via ``_PlaywrightPageDriver`` and at
            # runtime via ``ActorPageDriver``.
            if expand_selector is not None:
                clicks = await expand_all(driver, expand_selector)
                print(f"   expand-selector '{expand_selector}': {clicks} click(s)")

            # Bake + serialize the top document (state 1).
            top_html, _ = await _bake_and_serialize(page)

            # Enumerate captureable frames for state 1. Identical walk
            # to the pre-SYS-5 flow, now expressed once in
            # ``_capture_same_origin_frames`` so the SYS-13 state loop
            # below can freeze each state's frames with exactly the same
            # semantics (see that helper's docstring for why per-state
            # frames stopped being a non-goal).
            frames = await _capture_same_origin_frames(page, top_origin)

            pages: list[tuple[int, str, str]] = []
            states: list[
                tuple[
                    int,
                    str,
                    str,
                    list[tuple[str, str, str]],
                    list[tuple[int, str, str]],
                ]
            ] = []
            extracted: int

            def _bake_pages_into(
                sink: list[tuple[int, str, str]],
            ) -> Callable[[int], Awaitable[None]]:
                # Walker callback factory: bake each state ≥ 2 the walker
                # announces into ``sink``. State 1 is always baked by the
                # caller before the walker starts, so ``on_state(1)`` is
                # a no-op from the capture's perspective. A factory rather
                # than one closure because the declaring branch needs a
                # fresh sink per pre-filter state.
                async def _on_state(state_idx: int) -> None:
                    if state_idx == 1:
                        return
                    state_url = page.url
                    state_html, _ = await _bake_and_serialize(page)
                    sink.append((state_idx, state_url, state_html))

                return _on_state

            async def _walk(sink: list[tuple[int, str, str]]) -> set[str]:
                # SYS-12: thread ``next_control_selector`` through to
                # the walker as a per-state override — if set, the
                # walker's next-control JS skips signals 1–6 and
                # single-shot-matches the CSS selector instead
                # (documented in ``find_next_control.js``). ``driver``
                # is the same adapter used for css injection and
                # expansion above. Bound to a typed local before
                # returning: the pre-commit mypy hook type-checks only
                # the staged files, where the collector import resolves
                # to ``Any`` and a bare ``return await`` trips
                # ``no-any-return`` — the same reason ``_single_shot``
                # below binds its result.
                collected: set[str] = await walk_and_collect(
                    driver,
                    prefix,
                    top_origin,
                    min_depth,
                    on_state=_bake_pages_into(sink),
                    next_control_override=next_control_selector,
                    suppress_selector=suppress_selector,
                )
                return collected

            async def _single_shot() -> list[str]:
                result: list[str] = await page.evaluate(
                    EXTRACT_JOB_LINKS_JS,
                    [prefix, top_origin, min_depth, suppress_selector],
                )
                return result

            if pre_filter_urls:
                # SYS-13 agent-less multi-state union. State 1 was
                # already rendered, hooked, and baked above; now we run
                # the matcher against state 1 to seed the union, then
                # walk states 2..N applying the same execution order
                # (goto → wait → scroll → hooks 1–2 → bake → frames)
                # and unioning per-state matcher runs. Each state's
                # same-origin frames are frozen alongside it, so an
                # iframe-hosted listing replays at every state and not
                # just the first. With ``paginate`` on, the walker runs
                # *within* each state — the same composition
                # ``_extract_prefiltered`` performs at runtime by
                # forwarding ``Company.paginate`` into every per-state
                # ``collect_job_links`` call. State 1's pages land in
                # the top-level ``pages`` (the key already means
                # "pagination states of the top document"); state N's
                # land in the 5th element of its ``states`` tuple.
                union: set[str] = set()
                if paginate:
                    union.update(await _walk(pages))
                else:
                    union.update(await _single_shot())
                for idx, url in enumerate(pre_filter_urls[1:], start=2):
                    await page.goto(url)
                    await asyncio.sleep(wait_s)
                    for _ in range(scroll_n):
                        await page.evaluate(
                            "() => { window.scrollBy(0, window.innerHeight); }"
                        )
                        await asyncio.sleep(1)
                    # Hooks 1–2 re-run per state so the frozen HTML for
                    # each state reflects the same DOM the runtime
                    # collector sees. ``driver`` wraps ``page`` and is
                    # safe to reuse across navigations.
                    if pre_extract_css is not None:
                        await apply_pre_extract_css(driver, pre_extract_css)
                    if expand_selector is not None:
                        await expand_all(driver, expand_selector)
                    # The state's URL and first-page HTML are frozen
                    # *before* any walker click, so ``state-N.html`` is
                    # the state's landing page and its URL is the one the
                    # runtime navigated to.
                    state_url = page.url
                    state_html, _ = await _bake_and_serialize(page)
                    # Freeze this state's same-origin frames too. Boards
                    # that keep their listing in an iframe (iCIMS) carry
                    # zero anchors in the state's top document, so without
                    # this the frozen state is an empty shell.
                    state_frames = await _capture_same_origin_frames(page, top_origin)
                    state_pages: list[tuple[int, str, str]] = []
                    if paginate:
                        union.update(await _walk(state_pages))
                    else:
                        union.update(await _single_shot())
                    states.append(
                        (idx, state_url, state_html, state_frames, state_pages)
                    )
                extracted = len(union)
            elif paginate:
                # Drive the runtime walker and bake each state ≥ 2 as
                # the walker announces it via ``on_state``.
                extracted = len(await _walk(pages))
            else:
                # Single-shot sanity count — byte-identical to the
                # pre-SYS-5 code path.
                urls = await page.evaluate(
                    EXTRACT_JOB_LINKS_JS,
                    [prefix, top_origin, min_depth, suppress_selector],
                )
                extracted = len(urls)

            return top_html, frames, pages, states, extracted
        finally:
            await browser.close()


def _confirm_overwrite(out_dir: Path, *, force: bool) -> bool:
    """Return True if it is safe to write into ``out_dir``.

    A non-existent directory is always safe. An existing directory is
    overwritten silently when ``force`` is set; otherwise the user is
    prompted interactively.
    """
    if not out_dir.exists():
        return True
    if force:
        return True
    print(f"Snapshot already exists at {out_dir}.")
    response = input("Overwrite? [y/N] ").strip().lower()
    return response in ("y", "yes")


def _write_snapshot(
    out_dir: Path,
    company: Company,
    html: str,
    frames: list[tuple[str, str, str]],
    pages: list[tuple[int, str, str]],
    states: list[
        tuple[int, str, str, list[tuple[str, str, str]], list[tuple[int, str, str]]]
    ],
    expected: int,
    notes: str,
    *,
    pre_extract_css: str | None = None,
    expand_selector: str | None = None,
    next_control_selector: str | None = None,
) -> None:
    """Persist page.html, captured frames/pages/states, and metadata.json."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "page.html").write_text(html, encoding="utf-8")

    # Clear any stale ``frames/`` from a previous capture so the directory
    # only reflects the current run. Only remove files we would recreate —
    # never touch anything outside the frames subtree.
    frames_dir = out_dir / "frames"
    if frames_dir.exists():
        for old in frames_dir.glob("*.html"):
            old.unlink()

    frames_meta: list[dict[str, str]] = []
    if frames:
        frames_dir.mkdir(exist_ok=True)
        for name, url, frame_html in frames:
            filename = f"{name}.html"
            (frames_dir / filename).write_text(frame_html, encoding="utf-8")
            frames_meta.append({"file": f"frames/{filename}", "url": url})

    # Clear any stale ``pages/`` from a previous capture using the same
    # hygiene as ``frames/``. Boards captured without ``--paginate`` on
    # this run get any pre-existing ``pages/`` swept out, so the fixture
    # never carries orphan state-N files from a prior paginated capture.
    pages_dir = out_dir / "pages"
    if pages_dir.exists():
        for old in pages_dir.glob("*.html"):
            old.unlink()

    pages_meta: list[dict[str, Any]] = []
    if pages:
        pages_dir.mkdir(exist_ok=True)
        for state_idx, url, page_html in pages:
            filename = f"page-{state_idx}.html"
            (pages_dir / filename).write_text(page_html, encoding="utf-8")
            pages_meta.append({"file": f"pages/{filename}", "url": url})

    # SYS-13: ``states/`` mirrors ``pages/`` hygiene — cleared on every
    # capture so a fixture converted from declaring back to single-shot
    # (or vice-versa) never carries stale state-N files. Only the
    # capture-emitted state-N indexes get files; state 1 lives in the
    # top-level ``page.html`` as with every other fixture shape.
    states_dir = out_dir / "states"
    if states_dir.exists():
        for old in states_dir.glob("*.html"):
            old.unlink()
        # Per-state frame subtrees follow the same hygiene one level
        # deeper: a re-capture whose state N no longer has frames (or has
        # fewer of them) must not leave orphans behind to be replayed.
        for old in states_dir.glob("state-*-frames/*.html"):
            old.unlink()
        # ... and per-state page subtrees (declaring × paginate): a
        # re-capture with fewer walker pages in state N must not leave
        # an orphan ``page-M.html`` behind to be unioned back in.
        for old in states_dir.glob("state-*-pages/*.html"):
            old.unlink()

    states_meta: list[dict[str, Any]] = []
    if states:
        states_dir.mkdir(exist_ok=True)
        for state_idx, url, state_html, state_frames, state_pages in states:
            filename = f"state-{state_idx}.html"
            (states_dir / filename).write_text(state_html, encoding="utf-8")
            entry: dict[str, Any] = {"file": f"states/{filename}", "url": url}
            # ``frames`` on a state entry is additive-optional exactly
            # like the top-level key: emitted only when the state
            # actually carries anchor-bearing same-origin frames, so a
            # multi-state fixture whose anchors live in the top document
            # keeps byte-identical metadata to its pre-fix shape.
            if state_frames:
                frames_subdir = states_dir / f"state-{state_idx}-frames"
                frames_subdir.mkdir(exist_ok=True)
                state_frames_meta: list[dict[str, str]] = []
                for name, frame_url, frame_html in state_frames:
                    frame_file = f"{name}.html"
                    (frames_subdir / frame_file).write_text(
                        frame_html, encoding="utf-8"
                    )
                    state_frames_meta.append(
                        {
                            "file": f"states/state-{state_idx}-frames/{frame_file}",
                            "url": frame_url,
                        }
                    )
                entry["frames"] = state_frames_meta
            # ``pages`` on a state entry follows the identical
            # additive-optional rule: emitted only when the walker
            # collected states ≥ 2 *within* this pre-filter state
            # (declaring × paginate), written under
            # ``states/state-N-pages/page-M.html`` — the sibling-directory
            # convention ``state-N-frames/`` established.
            if state_pages:
                pages_subdir = states_dir / f"state-{state_idx}-pages"
                pages_subdir.mkdir(exist_ok=True)
                state_pages_meta: list[dict[str, str]] = []
                for page_idx, page_url, page_html in state_pages:
                    page_file = f"page-{page_idx}.html"
                    (pages_subdir / page_file).write_text(page_html, encoding="utf-8")
                    state_pages_meta.append(
                        {
                            "file": f"states/state-{state_idx}-pages/{page_file}",
                            "url": page_url,
                        }
                    )
                entry["pages"] = state_pages_meta
            states_meta.append(entry)

    metadata: dict[str, Any] = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "company_name": company.name,
        "job_board_url": company.job_board_url,
        "sample_job_url": company.sample_job_url,
        "expected_unfiltered_count": expected,
        "captured_at": datetime.now(tz=UTC).isoformat(),
        "captured_with_filters": False,
        "notes": notes,
    }
    # Only carry the override in metadata when the Company entry sets one.
    # Companies on the default derivation path stay on the old schema so
    # existing snapshots are byte-for-byte comparable.
    if company.link_rule.path_prefix is not None:
        metadata["path_prefix"] = company.link_rule.path_prefix
    # ``min_depth`` follows the same conditional convention: only recorded
    # when the Company entry raises the floor above the default of 1. This
    # keeps every existing (default-floor) snapshot byte-for-byte comparable
    # under SNAPSHOT_SCHEMA_VERSION 2 — the new key is additive-optional.
    if company.link_rule.min_depth != 1:
        metadata["min_depth"] = company.link_rule.min_depth
    # ``suppress_ancestor_selector`` (SYS-14) follows the same
    # additive-optional convention — recorded only when the Company entry
    # sets one, so every pre-SYS-14 fixture stays byte-for-byte comparable.
    # Recording it is what lets the harness replay the frozen HTML under
    # the same matcher arguments the runtime used; a fixture captured with
    # suppression active would otherwise over-count on replay by exactly
    # the suppressed section's size.
    if company.link_rule.suppress_ancestor_selector is not None:
        metadata["suppress_ancestor_selector"] = (
            company.link_rule.suppress_ancestor_selector
        )
    # ``frames`` is only emitted when non-empty so simple single-doc boards
    # keep byte-stable metadata (v1 diff-compatible where possible).
    if frames_meta:
        metadata["frames"] = frames_meta
    # ``pages`` is likewise conditional — captures without ``--paginate``
    # never emit the key, so every existing SYS-2 fixture stays byte-for-
    # byte comparable under SYS-5.
    if pages_meta:
        metadata["pages"] = pages_meta
    # ``expand_selector`` (SYS-6) is another additive-optional key. Absent
    # from every pre-SYS-6 fixture; present only on fixtures whose capture
    # invocation passed ``--expand-selector``. Recorded verbatim so a
    # re-capture producing a byte-different HTML can be traced back to
    # the selector that mounted the anchors.
    if expand_selector is not None:
        metadata["expand_selector"] = expand_selector
    # ``pre_extract_css`` and ``next_control_selector`` (SYS-12) follow
    # the same additive-optional convention. Recorded verbatim from
    # ``Company.hooks`` when set, so a re-capture producing a
    # byte-different HTML can be traced back to the hook payload that
    # produced it. Absent from every pre-SYS-12 fixture and from every
    # SYS-12 fixture whose Company has the inert-default hook.
    if pre_extract_css is not None:
        metadata["pre_extract_css"] = pre_extract_css
    if next_control_selector is not None:
        metadata["next_control_selector"] = next_control_selector
    # SYS-13 additive-optional keys. All three are present together (or
    # not at all): a declaring capture emits ``pre_filter_urls`` verbatim
    # so the runtime code path is auditable from the fixture, ``top_url``
    # so the harness knows which URL to replay ``page.html`` under (the
    # runtime never visits ``job_board_url`` on this path), and ``states``
    # so the harness enumerates the additional state-N files to union.
    # Non-declaring fixtures skip all three and stay byte-identical to
    # their pre-SYS-13 shape.
    if company.pre_filter_urls:
        metadata["pre_filter_urls"] = list(company.pre_filter_urls)
        metadata["top_url"] = company.pre_filter_urls[0]
    if states_meta:
        metadata["states"] = states_meta
    (out_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Capture a rendered-HTML snapshot for the regression test corpus."
        ),
    )
    parser.add_argument(
        "-c",
        "--company",
        required=True,
        help="Company handle (alias, acronym, or name substring).",
    )
    parser.add_argument(
        "--expected",
        type=int,
        default=None,
        help=(
            "Expected unfiltered job count. Locks in the regression target. "
            "If omitted, the extractor's current count is used (only safe for "
            "brand-new integrations where the matcher is already correct)."
        ),
    )
    parser.add_argument(
        "--wait",
        type=int,
        default=RENDER_WAIT_SEC,
        help="Seconds to wait after navigation for JS rendering (default %(default)s).",
    )
    parser.add_argument(
        "--scroll",
        type=int,
        default=RENDER_SCROLL_COUNT,
        help="Viewport-height scrolls to perform after the wait (default %(default)s).",
    )
    parser.add_argument(
        "--notes",
        default="",
        help="Optional free-text notes about the board (saved in metadata).",
    )
    # ``--paginate`` and ``--expand-selector`` are mutually exclusive at
    # the argparse layer: SYS-6 has never validated the interaction and
    # no board in the current corpus needs both. If a board later demands
    # both (e.g. paginate through pages whose per-page listings are
    # accordion-hidden), this mutex is where that decision reopens.
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--paginate",
        action="store_true",
        help=(
            "Drive the SYS-5 pagination walker while capturing. States "
            "≥ 2 are baked into 'pages/page-N.html' with a conditional "
            "'pages' entry in metadata; state 1 uses the existing "
            "'page.html' + 'frames/' layout. On a company declaring "
            "pre_filter_urls the walker runs within each state: state 1's "
            "pages use 'pages/', each later state's use "
            "'states/state-N-pages/'. Omit for the byte-identical "
            "pre-SYS-5 single-state capture. Mutually exclusive with "
            "--expand-selector."
        ),
    )
    mode.add_argument(
        "--expand-selector",
        default=None,
        metavar="CSS",
        help=(
            "SYS-6 accordion-reveal: after render+scroll, click every "
            "visible element matching this CSS selector in bounded "
            "rounds before baking, so anchors mounted on expand are "
            "captured in the frozen HTML. Recorded as "
            "metadata.expand_selector. Mutually exclusive with --paginate. "
            "SYS-12: when Company.hooks.expand_selector is set, this "
            "flag overrides the catalog value for iteration; leave "
            "unset to use the catalog selector unchanged."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing snapshot without prompting.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    company = find_company(args.company)
    if company is None:
        print(
            f"Company '{args.company}' not found. "
            "See `uv run job-agent-lab --help` for valid handles."
        )
        sys.exit(1)

    slug = slugify(company.name)
    out_dir = SNAPSHOTS_DIR / slug

    # SYS-13 × SYS-5: ``--paginate`` on a declaring company is supported.
    # The walker runs within each pre-filter state, as the runtime's
    # ``_extract_prefiltered`` does; state 1's pages land in the
    # top-level ``pages/`` and each later state's in
    # ``states/state-N-pages/``. See the module docstring.

    if not _confirm_overwrite(out_dir, force=args.force):
        print("Aborted.")
        sys.exit(1)

    print(f"Capturing snapshot for {company.name} -> {out_dir.relative_to(Path.cwd())}")
    # SYS-12: resolve the effective ``expand_selector`` (flag override
    # beats catalog) and pull the other two hooks straight from the
    # catalog. All three are forwarded into both ``_capture`` (where
    # they drive execution) and ``_write_snapshot`` (where they are
    # recorded verbatim in metadata for provenance).
    effective_expand_selector = resolve_expand_selector(
        company.hooks, args.expand_selector
    )
    pre_extract_css = company.hooks.pre_extract_css
    next_control_selector = company.hooks.next_control_selector
    try:
        html, frames, pages, states, extracted = asyncio.run(
            _capture(
                company.job_board_url,
                company.sample_job_url,
                args.wait,
                args.scroll,
                path_prefix=company.link_rule.path_prefix,
                min_depth=company.link_rule.min_depth,
                suppress_selector=company.link_rule.suppress_ancestor_selector,
                paginate=args.paginate,
                pre_extract_css=pre_extract_css,
                expand_selector=effective_expand_selector,
                next_control_selector=next_control_selector,
                pre_filter_urls=company.pre_filter_urls,
            )
        )
    except KeyboardInterrupt:
        sys.exit(130)

    expected: int
    if args.expected is None:
        expected = extracted
        print(
            f"WARNING: no --expected given; recording extracted count "
            f"({extracted}) as the target. This is only safe for brand-new "
            f"integrations where the matcher is already correct."
        )
    else:
        expected = args.expected
        if args.expected != extracted:
            print(
                f"WARNING: extracted count ({extracted}) differs from "
                f"--expected ({args.expected}). Snapshot will record "
                f"expected={args.expected}; the test will fail until the "
                f"matcher returns {args.expected} or --expected is corrected."
            )

    _write_snapshot(
        out_dir,
        company,
        html,
        frames,
        pages,
        states,
        expected,
        args.notes,
        pre_extract_css=pre_extract_css,
        expand_selector=effective_expand_selector,
        next_control_selector=next_control_selector,
    )

    size_kb = (out_dir / "page.html").stat().st_size / 1024
    print(f"Snapshot saved: {out_dir.relative_to(Path.cwd())} ({size_kb:.1f} KB)")
    if frames:
        print(f"   frames captured: {len(frames)}")
    if pages:
        print(f"   pages captured: {len(pages)} (states 2..{1 + len(pages)})")
    if states:
        print(f"   states captured: {len(states)} (states 2..{1 + len(states)})")
    state_page_total = sum(len(state[4]) for state in states)
    if state_page_total:
        print(f"   per-state pages captured: {state_page_total}")
    if effective_expand_selector is not None:
        print(f"   expand-selector recorded: {effective_expand_selector!r}")
    if pre_extract_css is not None:
        print(f"   pre-extract-css recorded ({len(pre_extract_css)} chars)")
    if next_control_selector is not None:
        print(f"   next-control-selector recorded: {next_control_selector!r}")
    print(f"   expected: {expected}  extracted-now: {extracted}")


if __name__ == "__main__":
    main()
