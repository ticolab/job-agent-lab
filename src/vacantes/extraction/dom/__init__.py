"""DOM-based job-link extraction.

Two JavaScript assets live under ``extraction/dom/assets/`` and are the
**single sources of truth** for the deterministic in-page work:

- ``collect_links.js`` (loaded as :data:`EXTRACT_JOB_LINKS_JS`) — the
  matcher. Walks the current DOM state and returns the list of job-link
  hrefs.
- ``find_next_control.js`` (loaded as :data:`FIND_NEXT_CONTROL_JS`,
  SYS-5) — the pagination-discovery pass. Locates the "next page"
  affordance by generic signals, stamps it with a ``data-jal-next``
  marker attribute for a driver-side click, and returns a descriptor.
  Only consulted when ``Company.paginate`` is ``True``; the single-shot
  code path never loads it.

Both assets are loaded via ``importlib.resources`` so runtime, capture
script, snapshot tests, and the skill's ground-truth script all execute
the exact same bytes in the page.

The matcher (v2, SYS-2) walks the top document plus every open shadow root
and same-origin ``iframe``/``frame`` ``contentDocument`` reachable from it,
in-page. Cross-origin and closed-shadow subtrees are structurally opaque
and skipped. Each candidate anchor is gated on CSS visibility
(``checkVisibility({checkVisibilityCSS: true})`` with an
``offsetParent``/``position: fixed`` fallback), and its URL fragment is
stripped before deduplication so ``/jobs/123`` and ``/jobs/123#apply``
collapse to one.

The Python-side rationale (why the function is public, the Lever id-in-path
vs id-in-query fix, the two-shape match) lives in the JS file's own comments
and in ``CLAUDE.md``/``TABNINE.md``.
"""

from importlib.resources import files

# The matcher takes ``[basePath, careerOrigin, minDepth, suppressSelector]``
# — the last two defaulted (``1`` and ``null``) so pre-SYS-3 / pre-SYS-14
# callers passing a shorter array keep exact current behaviour — and returns
# a list of href strings for anchors that match the job-link pattern:
# same-origin, visible, not inside a suppressed container, fragment-
# normalised, and either the path is a deeper segment of basePath
# (id-in-path shape, gated on ``minDepth``) or the path equals basePath with
# a non-empty query string (id-in-query shape). When both shapes fire on the
# same page, id-in-path wins (the Lever fix — id-in-query on the listing
# root is a filter facet, not a real job).
#
# ``suppressSelector`` (SYS-14) drops anchors whose ``closest(selector)`` is
# non-null, applied after the visibility gate and before bucketing. It
# closes C16 — sections whose anchors are URL-indistinguishable from real
# postings (Ulteig's UKG "Featured opportunities"). Invalid selectors throw
# a named error rather than no-opping; see the asset's header comment for
# the shadow/frame boundary semantics.
EXTRACT_JOB_LINKS_JS: str = (
    files("vacantes.extraction.dom")
    .joinpath("assets/collect_links.js")
    .read_text(encoding="utf-8")
)

# The pagination-discovery pass takes a ``[dryRun]`` array (dryRun defaults
# to false) and returns ``{found, signal, text}``. On found=true and
# dryRun=false the winning element is stamped with the ``data-jal-next``
# attribute so Python-side code can locate and click it via a driver
# selector. Signal priority (rel=next → aria-label /next/i → text /^next$/i
# → numeric successor) and the visibility / disabled / tag-shape guardrails
# live in the JS file's own comments. Only invoked from ``walk_and_collect``
# in ``collector.py``; the single-shot ``paginate=False`` code path never
# touches it.
FIND_NEXT_CONTROL_JS: str = (
    files("vacantes.extraction.dom")
    .joinpath("assets/find_next_control.js")
    .read_text(encoding="utf-8")
)
