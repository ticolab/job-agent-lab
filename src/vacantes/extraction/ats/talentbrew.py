"""Talentbrew (Radancy) results-API adapter.

Fourth extraction strategy through the port (after ``dom``,
``greenhouse``, ``phenom``). Talentbrew hosts a tenant-owned search
widget backed by a single auth-free JSON endpoint: ``GET
{origin}/search-jobs/results`` answers with an envelope carrying two
HTML fragments (``filters``, ``results``) and two boolean flags
(``hasJobs``, ``hasContent``). This adapter issues that GET, parses
``<a href>`` values out of ``results`` with stdlib
:class:`html.parser.HTMLParser`, absolutizes them, applies the
company's :class:`LinkRule` in Python via :func:`apply_link_rule`, and
returns a report authored by
:func:`~vacantes.extraction.base.build_report`. No rendered page,
no DOM, no LLM, which is why the report's ``metadata.model`` /
``agent_steps`` / ``agent_completed`` / ``agent_had_errors`` are all
``None`` (rendered as ``n/a`` by ``print_summary``).

Why an API adapter — the C12 closure for Citi
---------------------------------------------
Citi shares BCG's C12 shape (see
``blockers/INTEGRATION_BLOCKERS.md``): 3,529 unfiltered postings
sit behind a country facet whose page-level UI does not encode the
facet into the URL, so the SYS-5 walker neither reaches everything
(``MAX_PAGES=20`` against ~236 pages) nor can be frozen into a
snapshot that replays in the filtered state. The prerequisite probe
corrected the platform reading — ``/widgets`` 404s and Citi's assets
are served from ``tbcdn.talentbrew.com`` — so BCG's Phenom template
does not apply directly. Talentbrew's own contract is cleaner: the
facet *is* URL-addressable at the API layer (via ``FacetFilters[0]``
query parameters), no session cookie or CSRF token is required, and
the response is a plain JSON envelope ready to parse. Replacing the
un-freezable DOM snapshot with a recorded API payload
(``tests/fixtures/api/talentbrew/<handle>.json``) is Citi's C12
closure — a Talentbrew company deliberately has no ``page.html``.

Request contract
----------------
``GET {job_board_url origin}{talentbrew.results_path}`` with the
query parameters built by :func:`build_query_params`; see that
function's docstring for the config-derived / per-page / captured-
constant split. Two findings from the live evidence on 2026-07-18
shape the runtime posture. First, no auth is required — the endpoint
answers HTTP 200 with ``content-type: application/json`` on a bare
GET with no cookie or CSRF token. Second, the response envelope is
exactly ``{filters, results, hasJobs, hasContent}``; the ``filters``
fragment renders the facet UI and this adapter never parses it, only
``results`` is walked for anchors. The minimal-headers question was
resolved on 2026-08-04: the endpoint answers HTTP 200 to a bare GET
with only :class:`httpx`'s defaults (``host``, ``accept: */*``,
``accept-encoding``, ``connection``, ``user-agent``), so this
adapter sends no application headers of its own. The recorded
evidence curl's extra browser headers (``referer``, ``sec-ch-ua``,
``x-requested-with``, an explicit browser ``user-agent``) are all
UI-state noise; ``test_no_csrf_or_referer_headers_are_required``
pins the empty-header posture so a regression that quietly starts
injecting one fails loudly.

Anchor filtering — Python mirror of the JS matcher
--------------------------------------------------
``results`` is an HTML string, not a JSON list of job records, and it
contains both real job anchors and chrome (pager links, breadcrumbs,
filter-summary chips) that must be excluded. This adapter *does not*
run the browser's JS matcher; it uses :func:`apply_link_rule` — a
Python mirror of the URL-layer semantics implemented by
``src/vacantes/extraction/dom/assets/collect_links.js``, kept in
parity via ``tests/snapshots/test_linkrule_parity.py``. The two
engines are executed against the same synthetic anchor document and
their outputs asserted equal, so any URL-semantic change to the JS
asset fails the parity test mechanically rather than depending on
anyone remembering a convention. See :func:`apply_link_rule`'s
docstring for what is mirrored (URL layer) versus deliberately not
mirrored (DOM-layer concerns like visibility, container suppression,
shadow-root descent — Talentbrew renders its fragment as light DOM in
one document, none of those axes apply).

Region filtering is server-side only: the facet-applied GET *is* the
filter. Talentbrew records carry no structured location field that
could be re-verified client-side, and per-card location text
(``<span class="job-location">``) is a fragile second contract that
would drift independently of the primary facet. Rot in the facet id
is owned by SYS-9's ``verdict`` layer on the next live run.

Pagination
----------
The endpoint returns one page at a time indexed by ``CurrentPage``.
Continue to page N+1 iff the LinkRule-filtered anchor count for page
N equals ``records_per_page`` **and** ``hasJobs=true``. Two signals
because the raw parsed count is perturbed by pager/chrome anchors
that LinkRule drops (so a partial last page can still have
``records_per_page`` raw anchors), and ``hasJobs`` alone would
terminate one page too late in some tenants. A defensive
``_MAX_PAGES=20`` cap mirrors the SYS-5 walker: a tenant genuinely
requiring more pages fires a loud warning and emits the union
collected so far, letting the verdict layer surface the shortfall on
the next live run.

Citi's Costa Rica facet returns exactly 10 postings on 2026-08-04 —
below the 15-default ``records_per_page`` — so the multi-page code
path has been end-to-end verified only under synthesized respx
payloads (see ``TestPagination``, ``TestMaxPagesCap`` in
``tests/unit/test_talentbrew.py``). A broader-facet live probe (e.g.
Citi's all-locations page, which the widget shows overflowing) was
deliberately deferred: the synth tests already pin the continuation
predicate, ordered dedup, ``hasJobs=false`` terminator, and
``_MAX_PAGES`` cap against the exact envelope shape captured from
the live endpoint, so a live multi-page run would only exercise the
same client code against a real second page — no new contract
surface. The first Talentbrew tenant whose filtered region genuinely
spans multiple pages is the trigger to live-verify the loop, and
any request-parameter drift (e.g. an ``IsPagination=True`` toggle
required for pages ≥ 2) will surface as a partial page-2 count that
SYS-9's verdict layer flags on the next run.

Failure semantics
-----------------
- Transport error or 5xx: one retry, then error report (see
  :func:`_fetch_results_page`).
- Non-200 after retry: error report.
- 200 with envelope-shape violations (``results`` not a string,
  ``hasJobs`` not a bool): loud error, not silent empty. These are
  contract breakages.
- 200 on page 1 with ``hasJobs=true`` and zero anchors parsed from
  ``results``: loud error. The endpoint promising jobs but the
  fragment carrying none is a fragment-shape drift worth surfacing
  rather than swallowing.
- 200 on page 1 with parsed anchors but *zero* LinkRule-survivors:
  loud error naming ``path_prefix`` and ``min_depth``. Almost always
  means the wrong ``path_prefix`` for the tenant, or the facet
  applied to a different board than the LinkRule targets.
- 200 on page 1 with ``hasJobs=false``: honest empty (``jobs=[]``,
  ``error=None``). Legitimate outcome for a tenant with no
  region-matching postings today; the verdict layer classifies it.
- 200 on pages ≥ 2 with zero survivors or ``hasJobs=false``:
  legitimate pagination terminator, not an error.
- Missing ``talentbrew`` config or ``link_rule.path_prefix``: error
  report (config faults surfaced without raising).
- Any other exception: caught and folded into an error report —
  :meth:`TalentbrewStrategy.extract` never raises out.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from html.parser import HTMLParser
from typing import Any, ClassVar
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit

import httpx

from vacantes.domain.company import Company, TalentbrewConfig
from vacantes.extraction.base import RunContext, build_report

logger = logging.getLogger(__name__)

# Total wall-clock budget for a single HTTP call (connect + read).
# Mirrors the Phenom / Greenhouse adapters. Citi's ~96 KiB response
# lands well inside it.
_REQUEST_TIMEOUT = httpx.Timeout(30.0)

# Defensive cap on the pagination loop. Mirrors the SYS-5 walker's
# ``MAX_PAGES``. A tenant that genuinely needs more pages is a signal
# to live-verify the ``IsPagination`` toggle in
# :func:`build_query_params` and lift the cap, not to raise it
# speculatively.
_MAX_PAGES = 20


class _AnchorHrefCollector(HTMLParser):
    """Collect the ``href`` attribute of every ``<a>`` tag in order.

    Uses :class:`html.parser.HTMLParser`'s default
    ``convert_charrefs=True`` so entity references such as ``&amp;``
    appearing inside attribute values are unescaped for free — the one
    behaviour the Talentbrew results fragment exercises (entities show
    up in titles, not hrefs, but a synthetic-href test pins the
    guarantee end-to-end).
    """

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value is not None:
                self.hrefs.append(value)
                return


def parse_anchor_hrefs(fragment: str) -> list[str]:
    """Return every ``<a href>`` value in ``fragment`` in document order.

    Non-anchor tags are ignored. Anchors without an ``href`` attribute
    are ignored (they carry no navigable URL — a bare ``<a name="…">``
    or a JS-only widget hook). Duplicates are preserved here; the
    :func:`apply_link_rule` mirror is the sole place that dedups, so
    the parity test can exercise the mirror's dedup behaviour on real
    duplicates.

    The parser is tolerant: :class:`html.parser.HTMLParser` never
    raises on malformed HTML, so the caller cannot rely on this
    function to surface a broken fragment. The Talentbrew adapter
    instead validates the JSON envelope shape (type of ``results``,
    type of ``hasJobs``) and applies two page-1 semantic guards.

    Args:
        fragment: The value of ``envelope["results"]`` — an HTML
            fragment (not a full document; no ``<html>`` wrapper).

    Returns:
        Every ``href`` value in the fragment, in the order the
        parser encountered them, with HTML entities already decoded.
    """
    collector = _AnchorHrefCollector()
    collector.feed(fragment)
    collector.close()
    return collector.hrefs


def apply_link_rule(
    urls: Iterable[str],
    *,
    origin: str,
    base_path: str,
    min_depth: int = 1,
) -> list[str]:
    """Python mirror of ``collect_links.js`` — URL-layer only.

    This is the one genuinely novel artifact in SYS-17: a deliberate,
    parity-tested duplication of the JS matcher's URL semantics. The
    parity test in ``tests/snapshots/test_linkrule_parity.py``
    executes the *real* JS asset against a synthetic anchor document
    and asserts set-equality with this function's output over the
    same absolutized href list — so any future URL-layer change to
    ``collect_links.js`` that this function fails to track fails the
    parity test mechanically, without depending on anyone remembering
    a convention.

    What is mirrored (the URL layer)
    --------------------------------
    - ``URL`` parse-or-skip: unparseable inputs are dropped silently.
    - Origin equality against ``origin`` (relative URLs, which parse
      to empty scheme/netloc, are also dropped).
    - Fragment strip (``#…`` is removed from the emitted URL, so
      ``/jobs/123`` and ``/jobs/123#apply`` collapse into one entry).
    - Trailing-slash strip on the path for the bucketing check only.
      A single trailing ``/`` is stripped, matching JS ``.replace(/\\/$/, '')``
      (Python's ``str.rstrip("/")`` would strip a run and diverge on
      pathological ``/jobs//`` inputs; the slice form used below
      matches JS exactly).
    - Id-in-path bucket: path starts with ``base_path + "/"`` and the
      remainder splits into at least ``min_depth`` segments.
    - Id-in-query bucket: path equals ``base_path`` and the query is
      non-empty.
    - Path-bucket preference: when both buckets contain URLs, only
      the path bucket is emitted (Lever-style boards where the
      query-shape on the listing root is a filter facet, not a real
      posting; a path bucket fully drained by ``min_depth`` falls
      back to the query bucket, indistinguishable from a naturally
      empty one).
    - First-seen order preservation and set-based dedup on the
      emitted URL string.

    What is deliberately NOT mirrored (the DOM layer)
    -------------------------------------------------
    - Visibility gating (``checkVisibility`` / ``offsetParent`` /
      ``position: fixed``). This mirror operates on a URL list, not
      a live DOM; visibility is a property of a rendered element.
    - Container suppression (``anchor.closest(suppress_selector)``).
      Same reason: it requires a live DOM ancestor walk.
    - Shadow-root descent and same-origin frame descent. Talentbrew
      renders its fragment as light DOM in the same document; there
      are no shadow roots or nested frames in the API path.

    A Talentbrew tenant that ever required any of the omitted axes
    would be a signal to route it through the DOM strategy instead —
    the API path exists precisely because the platform's canonical
    surface is the JSON envelope, not the rendered board.

    Args:
        urls: An iterable of URL strings. The adapter is responsible
            for pre-absolutizing relative hrefs against the response
            document's base URL before calling; a relative URL that
            reaches this function is silently dropped by the origin
            gate. Empty strings are also silently dropped.
        origin: The expected scheme+netloc for career links, e.g.
            ``"https://jobs.citi.com"``. Anchors whose origin differs
            (off-site apply portals, marketing sub-domains) are
            dropped.
        base_path: The company's ``LinkRule.path_prefix``, e.g.
            ``"/job"``. The two-bucket check keys entirely off this
            value; a wrong ``path_prefix`` at integration time
            manifests as zero LinkRule-survivors and the Task 3
            adapter's page-1 guard raises loudly rather than emitting
            a silent empty.
        min_depth: The company's ``LinkRule.min_depth`` (default 1,
            matching the field default in
            :class:`~vacantes.domain.company.LinkRule`). Applied
            inside the id-in-path branch only.

    Returns:
        The filtered, deduplicated list of URL strings in first-seen
        order, with fragments removed and either the id-in-path
        bucket (preferred) or the id-in-query bucket returned.
    """
    deeper_path: list[str] = []
    seen_deeper: set[str] = set()
    prefix_with_query: list[str] = []
    seen_prefix: set[str] = set()

    for href in urls:
        if not href:
            continue
        try:
            parts = urlsplit(href)
        except ValueError:
            continue
        if not parts.scheme or not parts.netloc:
            # Relative URL (or ``mailto:``, ``javascript:``, etc.) —
            # cannot be same-origin, drop.
            continue
        link_origin = f"{parts.scheme}://{parts.netloc}"
        if link_origin != origin:
            continue

        # Fragment strip — emitted URL never carries ``#…``. The stored
        # key is the fragment-stripped URL string, mirroring the JS's
        # ``linkUrl.hash = ''; ... .add(linkUrl.href)``.
        emitted = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))

        # Trailing-slash strip for the bucketing check only. Match
        # ``.replace(/\\/$/, '')`` — at most one trailing slash. The
        # emitted URL retains the original path.
        link_path = parts.path
        if link_path.endswith("/"):
            link_path = link_path[:-1]

        if link_path.startswith(base_path + "/"):
            remainder = link_path[len(base_path) + 1 :]
            depth = len(remainder.split("/"))
            if depth >= min_depth and emitted not in seen_deeper:
                seen_deeper.add(emitted)
                deeper_path.append(emitted)
        elif link_path == base_path and parts.query:
            if emitted not in seen_prefix:
                seen_prefix.add(emitted)
                prefix_with_query.append(emitted)

    return deeper_path if deeper_path else prefix_with_query


def build_query_params(config: TalentbrewConfig, page: int) -> dict[str, str]:
    """Compose the query-string for one Talentbrew results GET.

    This is the single expression of the wire contract — no other
    function in this module builds request state. Field grouping:

    - **Config-derived variables** — ``ActiveFacetID``,
      ``FacetFilters[0].ID`` (both the tenant's opaque facet id),
      ``FacetFilters[0].Display`` (the human-visible label),
      ``RecordsPerPage`` (the tenant's page-size default, override
      only with live evidence).
    - **Per-page variable** — ``CurrentPage``.
    - **Captured constants (verbatim)** — every other key in the
      dict below. Per binding contract §4.8.2, the remaining fields
      of the captured curl are carried verbatim as constants; they
      are believed to be UI-state hints the server largely ignores
      (``FacetFilters[0].Count=10`` is a *capture-time* count of
      matched postings, not a request parameter, and the live probe
      returning 10 validates that reading). ``IsPagination=False``
      is also constant here even for pages ≥ 2: Citi CR fits on one
      page so the multi-page toggle has never been live-verified,
      and flipping it speculatively would be a change without
      evidence. A multi-page tenant arriving in the future is the
      trigger to live-verify the toggle.

    Values are all strings; :class:`httpx.AsyncClient` handles the
    URL-encoding (space → ``+``, ``[`` / ``]`` → percent-encoded).

    Args:
        config: The company's :class:`TalentbrewConfig` — supplies
            ``facet_id``, ``facet_display``, and ``records_per_page``.
        page: The 1-based page number, used for ``CurrentPage``.

    Returns:
        The full param dict ready to hand to
        ``client.get(url, params=...)``.
    """
    return {
        "ActiveFacetID": config.facet_id,
        "CurrentPage": str(page),
        "RecordsPerPage": str(config.records_per_page),
        "TotalContentResults": "",
        "Distance": "50",
        "RadiusUnitType": "0",
        "Keywords": "",
        "Location": "",
        "ShowRadius": "False",
        "IsPagination": "False",
        "CustomFacetName": "",
        "FacetTerm": "",
        "FacetType": "0",
        "FacetFilters[0].ID": config.facet_id,
        "FacetFilters[0].FacetType": "2",
        "FacetFilters[0].Count": "10",
        "FacetFilters[0].Display": config.facet_display,
        "FacetFilters[0].IsApplied": "true",
        "FacetFilters[0].FieldName": "",
        "SearchResultsModuleName": "Search Results",
        "SearchFiltersModuleName": "Search Filters",
        "SortCriteria": "5",
        "SortDirection": "0",
        "SearchType": "5",
        "PostalCode": "",
        "ResultsType": "0",
        "fc": "",
        "fl": "",
        "fcf": "",
        "afc": "",
        "afl": "",
        "afcf": "",
        "TotalContentPages": "NaN",
    }


async def _fetch_results_page(
    client: httpx.AsyncClient, url: str, params: dict[str, str]
) -> dict[str, Any]:
    """GET ``url`` with ``params`` with one retry on transport errors / 5xx.

    Kept private and separate from :meth:`TalentbrewStrategy.extract`
    so the retry policy lives in one documented place and is
    unit-testable through respx — the same split the Phenom and
    Greenhouse adapters use.
    """
    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            response = await client.get(url, params=params)
            if 500 <= response.status_code < 600 and attempt == 1:
                # First-attempt 5xx: retry. A second 5xx falls through
                # to ``raise_for_status`` below.
                logger.warning(
                    "Talentbrew API returned %s on attempt %s for %s; retrying",
                    response.status_code,
                    attempt,
                    url,
                )
                continue
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError(
                    f"Talentbrew API returned non-object body (type="
                    f"{type(payload).__name__}) from {url}."
                )
            return payload
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt == 1:
                logger.warning(
                    "Talentbrew API transport error on attempt %s for %s: %s; retrying",
                    attempt,
                    url,
                    exc,
                )
                continue
            raise
    # Unreachable: the loop either returns or re-raises above. Kept so
    # mypy sees a total function.
    raise RuntimeError(  # pragma: no cover
        f"Talentbrew fetch loop exited without returning ({last_exc!r})"
    )


class TalentbrewStrategy:
    """Talentbrew (Radancy) results-API adapter."""

    name: ClassVar[str] = "talentbrew"

    async def extract(self, company: Company, ctx: RunContext) -> dict[str, Any]:
        """Paginate the tenant's facet-filtered results endpoint.

        Never raises out — every failure mode (missing config,
        transport, envelope shape, semantic drift) is folded into an
        error report so the CLI's per-company loop stays intact.
        """
        start = time.time()
        error: str | None = None
        jobs: list[str] = []

        try:
            config = company.talentbrew
            if config is None:
                # Belt to the schema validator's braces: a hand-mutated
                # Company or one constructed outside the catalog could
                # reach here.
                raise ValueError(
                    f"strategy='talentbrew' requires a TalentbrewConfig on "
                    f"company={company.name!r}; got talentbrew=None."
                )

            path_prefix = company.link_rule.path_prefix
            if not path_prefix:
                raise ValueError(
                    f"strategy='talentbrew' requires link_rule.path_prefix on "
                    f"company={company.name!r} — the URL-layer LinkRule mirror "
                    f"keys entirely off it and Talentbrew's 'results' fragment "
                    f"mixes real job anchors with pager/chrome anchors that "
                    f"the mirror is what excludes."
                )

            parsed = urlparse(company.job_board_url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            url = f"{origin}{config.results_path}"
            min_depth = company.link_rule.min_depth

            # Ordered dedup across pages. ``dict`` insertion order is
            # the ordering of the emitted job list; we key on the URL
            # string and never read the values.
            seen_urls: dict[str, None] = {}

            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                page = 0
                for page in range(1, _MAX_PAGES + 1):
                    params = build_query_params(config, page)
                    envelope = await _fetch_results_page(client, url, params)

                    results_fragment = envelope.get("results")
                    has_jobs = envelope.get("hasJobs")
                    if not isinstance(results_fragment, str):
                        raise ValueError(
                            f"Talentbrew envelope for company={company.name!r} "
                            f"on page {page} has 'results' of type "
                            f"{type(results_fragment).__name__}, expected str."
                        )
                    if not isinstance(has_jobs, bool):
                        raise ValueError(
                            f"Talentbrew envelope for company={company.name!r} "
                            f"on page {page} has 'hasJobs' of type "
                            f"{type(has_jobs).__name__}, expected bool."
                        )

                    if not has_jobs:
                        # Page 1: honest empty (legitimate tenant with
                        # no region-matching postings today).
                        # Page ≥ 2: legitimate pagination terminator.
                        break

                    raw_hrefs = parse_anchor_hrefs(results_fragment)
                    absolutized = [urljoin(url, href) for href in raw_hrefs]
                    filtered = apply_link_rule(
                        absolutized,
                        origin=origin,
                        base_path=path_prefix,
                        min_depth=min_depth,
                    )

                    if page == 1:
                        # Loud page-1 guards. On pages ≥ 2 a zero-yield
                        # is a legitimate terminator (see below), so
                        # these checks are page-1-only.
                        if not raw_hrefs:
                            raise ValueError(
                                f"Talentbrew envelope for "
                                f"company={company.name!r} has hasJobs=true "
                                f"but zero anchors parsed from the 'results' "
                                f"fragment — likely fragment-shape drift."
                            )
                        if not filtered:
                            raise ValueError(
                                f"Talentbrew envelope for "
                                f"company={company.name!r} parsed "
                                f"{len(raw_hrefs)} anchors from 'results' but "
                                f"zero survived LinkRule "
                                f"(path_prefix={path_prefix!r}, "
                                f"min_depth={min_depth}) — likely path_prefix "
                                f"drift or the facet applied to a different "
                                f"board than the LinkRule targets."
                            )

                    for job_url in filtered:
                        if job_url not in seen_urls:
                            seen_urls[job_url] = None

                    # Pagination-continue: filtered count == page size
                    # AND hasJobs=true. A partial last page (fewer
                    # LinkRule-survivors than records_per_page)
                    # terminates cleanly, and pages ≥ 2 that yield
                    # zero survivors do too (this branch takes the
                    # ``<`` path because 0 < records_per_page).
                    if len(filtered) < config.records_per_page:
                        break
                else:
                    # for/else: loop exhausted ``_MAX_PAGES`` without
                    # breaking. Emit union collected; verdict layer
                    # surfaces the shortfall on the next live run.
                    logger.warning(
                        "Talentbrew pagination reached _MAX_PAGES=%s for "
                        "company=%s; emitting %s URLs collected so far. "
                        "Live-verify the tenant's true page count and lift "
                        "the cap if needed.",
                        _MAX_PAGES,
                        company.name,
                        len(seen_urls),
                    )

            jobs = list(seen_urls.keys())

        except Exception as exc:  # noqa: BLE001 — deliberate catch-all
            logger.exception("TalentbrewStrategy failed for company=%s", company.name)
            error = f"{type(exc).__name__}: {exc}"
            jobs = []

        elapsed = time.time() - start
        return build_report(
            strategy=self.name,
            company_name=company.name,
            company_url=company.job_board_url,
            jobs=jobs,
            elapsed=elapsed,
            model=None,
            agent_steps=None,
            agent_completed=None,
            agent_had_errors=None,
            error=error,
            expected_jobs=company.expected_jobs,
        )
