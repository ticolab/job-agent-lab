"""Phenom People ``refineSearch`` API strategy (SYS-15).

Phenom hosts its job-search widget on the *tenant's own* domain — BCG's
answers at ``https://careers.bcg.com/widgets`` — and serves the whole
filtered result set from a single ``POST``. This adapter issues that
request, filters by :meth:`~job_agent_lab.domain.region.TargetRegion.matches`,
and returns a report in the shape authored by
:func:`~job_agent_lab.extraction.base.build_report`. No rendered page,
no DOM, no LLM, which is why the report's ``metadata.model`` /
``agent_steps`` / ``agent_completed`` / ``agent_had_errors`` are all
``None`` (rendered as ``n/a`` by ``print_summary``).

Why an API adapter at all — the C12 closure
-------------------------------------------
BCG's DOM path is *runtime*-clean: the SYS-5 walker extracts its
filtered board correctly. What it cannot have is an honest snapshot
regression artifact. The country facet is sessionStorage state rather
than a URL parameter, so a captured page cannot be replayed in the
filtered state; and the unfiltered board's 863 postings dwarf the
walker's ``MAX_PAGES=20``, so any ``expected_unfiltered_count`` frozen
from a capture would be a walker-cap artifact rather than a property of
the board. Routing BCG through this adapter replaces the un-freezable
DOM snapshot with a recorded API payload
(``tests/fixtures/api/phenom/<tenant>.json``) — a regression artifact
that *is* stable. That substitution is the whole of C12's closure; a
Phenom company deliberately has no DOM snapshot.

Request contract
----------------
``POST {job_board_url origin}{phenom.endpoint_path}`` with the body
built by :func:`build_request_body`. Two findings from the live probe
on 2026-08-02 shape this:

- **No CSRF token or referer header is required.** The captured
  evidence curl carried ``x-csrf-token`` and ``referer`` headers, but
  the endpoint answers HTTP 200 with ``content-type: application/json``
  alone. Sending only the content-type keeps the adapter free of
  session bootstrapping (there is no page load to scrape a token from).
- **The proposal's minimal field list is sufficient verbatim.** The
  captured browser body carried a dozen extra UI-state fields
  (``deviceType``, ``sortBy``, ``jdsource``, ``isSliderEnable``, …);
  the trimmed template in :func:`build_request_body` returns the same
  results.

Region filtering is belt-and-braces, matching the Greenhouse adapter:
the request asks the server for the region via ``selected_fields``,
*and* every returned record is re-verified client-side. The facet value
sent to the server is ``region.filter_tokens[0]`` — the region's
canonical country name (``"Costa Rica"`` for
:data:`~job_agent_lab.domain.region.COSTA_RICA_LATAM`). Deliberately not
the whole preferred-half slice, which also contains ``"CR"``: that is a
UI-filter token for the DOM agent, not a Phenom facet value, and the
API returns nothing for it.

URL synthesis — the one inferred contract
-----------------------------------------
Phenom job objects carry no canonical board URL. ``applyUrl`` points at
an external applicant portal (``experiencedtalent.bcg.com/careerhub/…``)
rather than the board posting, so it is not a substitute. The board's
own posting URL is ``{origin}{path_prefix}/{jobId}/{slug(title)}``, and
this adapter synthesizes it — the only place in the R2 architecture
where an emitted URL is constructed rather than read from a payload.

Two guardrails follow from that. The path template comes from
``Company.link_rule.path_prefix`` and is never hardcoded, so a second
Phenom tenant with a different route needs config rather than a code
change; a missing ``path_prefix`` is a loud extract-time error (there is
nothing sensible to fall back to — ``derive_path_prefix`` on a sample
job URL would keep the jobId segment and silently synthesize garbage).
And because the slug rule is *inferred*, integrating any Phenom tenant
carries a mandatory verification step: check that at least three
synthesized URLs return HTTP 200 before the integration commit lands.
The documented fallback if that ever fails is to emit ``applyUrl``
verbatim with a ledger note — authoritative but off-origin. That
fallback is deliberately not implemented here: it would be dead code
guarding a case the BCG verification already disproves, and switching
to it is a code change that should come with the evidence that
motivated it.

Failure semantics
-----------------
- Transport error (``httpx.TransportError``) or 5xx: one retry, then an
  error report with ``metadata.error`` populated and empty ``jobs``.
- Non-200 after retry: error report.
- 200 with a body missing ``refineSearch`` / ``data`` / ``jobs``: error
  report.
- Missing ``phenom`` config or ``link_rule.path_prefix``: error report
  (both are schema/config faults, surfaced without raising).
- **200 with zero region matches: honest empty result, not an error**
  (``metadata.error`` is ``None``, ``jobs`` is ``[]``). A tenant with no
  Costa Rica postings today is different from a broken board.
- Any other exception: caught and folded into an error report —
  :meth:`PhenomStrategy.extract` never raises out, so the CLI's
  per-company loop survives.

Under-fetch is reported, not paginated. The request asks for
``size:100`` in one call; BCG's Costa Rica facet returns 14, roughly a
seventh of that. If a region ever exceeds the page size the adapter
emits what it received and logs a warning naming the shortfall — the
SYS-9 verdict layer then flips ``verdict="under"``, which is its job. A
pagination loop is deliberately not built ahead of a board that needs
one.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, ClassVar
from urllib.parse import urlparse

import httpx

from job_agent_lab.domain.company import Company, PhenomConfig
from job_agent_lab.domain.region import TargetRegion
from job_agent_lab.extraction.base import RunContext, build_report

logger = logging.getLogger(__name__)

# Total wall-clock budget for a single HTTP call (connect + read).
# Mirrors the Greenhouse adapter's constant; BCG's ~44 KiB filtered
# payload lands well inside it.
_REQUEST_TIMEOUT = httpx.Timeout(30.0)

# Records requested per call. One call is the whole contract: BCG's
# Costa Rica facet returns 14 against this ceiling. See the module
# docstring on why there is no pagination loop.
_PAGE_SIZE = 100

# Facet fields the widget asks the server to aggregate. Copied from the
# captured request; the adapter does not read the returned counts, but
# omitting the key changes the response shape.
_ALL_FIELDS = ("country", "city", "category", "company", "type", "jobType")

# Runs of anything that is not ASCII-alphanumeric collapse to one
# hyphen in a posting slug.
_SLUG_SEPARATOR_RE = re.compile(r"[^A-Za-z0-9]+")


def slugify_title(title: str) -> str:
    """Render a job title as the slug segment of a Phenom posting URL.

    Every run of non-alphanumeric characters collapses to a single
    hyphen and leading/trailing hyphens are trimmed. **Case is
    preserved** — Phenom's slugs are mixed-case
    (``Creative-Manager-Hybrid``), which is what distinguishes this from
    :func:`job_agent_lab.catalog.slugify`, which lowercases and exists
    to name output files and snapshot directories. The two look similar
    and must not be merged: lowercasing here would produce URLs the
    board does not serve.

    The rule is *inferred* from observed board URLs rather than
    documented by Phenom, which is why integrating a tenant requires
    verifying synthesized URLs return HTTP 200 (see the module
    docstring). Live-verified against BCG on 2026-08-02, including a
    title containing both ``&`` and `` - ``.

    Args:
        title: The job record's ``title`` field, verbatim.

    Returns:
        The slug segment. An empty string when ``title`` holds no
        alphanumeric characters at all — the caller is responsible for
        deciding what that means (this adapter skips such records
        rather than emitting a URL with an empty tail).
    """
    return _SLUG_SEPARATOR_RE.sub("-", title).strip("-")


def build_request_body(config: PhenomConfig, country_value: str) -> dict[str, Any]:
    """Build the ``refineSearch`` POST body for one tenant + region.

    This is the single expression of the wire contract — the adapter
    holds no other request-shaping logic, and the fields here are
    exactly the trimmed set the live probe proved sufficient (see the
    module docstring's request-contract section).

    Args:
        config: The tenant's :class:`PhenomConfig` (supplies ``pageId``
            and ``lang``).
        country_value: The server-side facet value, i.e. the region's
            canonical country name.

    Returns:
        A JSON-serialisable dict ready for ``client.post(json=...)``.
    """
    return {
        "lang": config.locale,
        "pageName": "search-results",
        "ddoKey": "refineSearch",
        "from": 0,
        "size": _PAGE_SIZE,
        "jobs": True,
        "counts": True,
        "all_fields": list(_ALL_FIELDS),
        "pageId": config.page_id,
        "siteType": "external",
        "selected_fields": {"country": [country_value]},
        "keywords": "",
        "global": True,
        "locationData": {},
    }


def synthesize_job_url(origin: str, path_prefix: str, job_id: str, title: str) -> str:
    """Compose a board posting URL from a job record's id and title.

    See the module docstring for why this is synthesized rather than
    read from the payload, and for the verification obligation that
    comes with it.
    """
    return f"{origin}{path_prefix}/{job_id}/{slugify_title(title)}"


def _record_matches_region(job: dict[str, Any], region: TargetRegion) -> bool:
    """Re-verify one record's location against ``region``, client-side.

    A record matches when **either** its primary ``country``/``city``
    pair matches **or** any entry in ``multi_location_array`` does.

    The multi-location arm is checked unconditionally, which is a
    deliberate widening of the contract sketched in
    ``ARCHITECTURE_PROPOSAL_R2.md`` §4.8.1 (it consults the array only
    "if the primary ``country`` is empty"). The recorded BCG payload
    disproves that narrower rule: job 57516,
    ``TEMP: Global Marketing Manager - Financial Institutions``, is a
    genuine Costa Rica posting returned under the Costa Rica facet, yet
    its primary fields read ``country="United Kingdom"``,
    ``city="London"`` with ``Heredia, Costa Rica`` appearing only in
    ``multi_location_array``. Phenom evidently promotes one location of
    a multi-location posting into the primary fields and there is no
    guarantee it picks an in-region one.

    Under the narrow rule that record is dropped and the adapter emits
    13 URLs where the server reports ``totalHits: 14`` and the queue
    expects 14 — a silent under-count that would bias precisely against
    the multi-region postings a global consultancy publishes most. The
    empty-primary-country case the proposal describes is a natural
    subset of the wider rule, so nothing the proposal intended is lost.
    """
    country = job.get("country") or ""
    city = job.get("city") or ""
    primary = f"{country} {city}".strip()
    if primary and region.matches(primary):
        return True

    entries = job.get("multi_location_array") or []
    if not isinstance(entries, list):
        return False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_country = entry.get("country") or ""
        entry_city = entry.get("city") or ""
        combined = f"{entry_country} {entry_city}".strip()
        # Tenants disagree on the entry shape. BCG emits discrete
        # ``country`` / ``city`` keys; Roche emits ``{latlong, location}``
        # where ``location`` is the pre-joined
        # ``"Sabana Norte, San Jose, Costa Rica"`` string and neither
        # discrete key exists. Reading only the discrete pair made the
        # arm a silent no-op on the second shape — the loop ran, matched
        # nothing, and dropped three genuine Costa Rica postings whose
        # primary fields were Budapest and Indianapolis, emitting 9
        # against a server-reported ``totalHits: 12``. Falling back to
        # the joined string keeps both shapes on the same code path;
        # the region predicate is substring-based and word-bounded, so
        # a joined string is exactly as safe an input as a joined pair.
        if not combined:
            combined = str(entry.get("location") or "").strip()
        if combined and region.matches(combined):
            return True
    return False


async def _fetch_refine_search(
    client: httpx.AsyncClient, url: str, body: dict[str, Any]
) -> dict[str, Any]:
    """POST ``body`` to ``url`` with one retry on transport errors / 5xx.

    Kept private and separate from :meth:`PhenomStrategy.extract` so the
    retry policy lives in one documented place and is unit-testable
    through respx — the same split the Greenhouse adapter uses.
    """
    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            response = await client.post(url, json=body)
            if 500 <= response.status_code < 600 and attempt == 1:
                # First-attempt 5xx: retry. A second 5xx falls through
                # to ``raise_for_status`` below.
                logger.warning(
                    "Phenom API returned %s on attempt %s for %s; retrying",
                    response.status_code,
                    attempt,
                    url,
                )
                continue
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError(
                    f"Phenom API returned non-object body (type="
                    f"{type(payload).__name__}) from {url}."
                )
            return payload
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt == 1:
                logger.warning(
                    "Phenom API transport error on attempt %s for %s: %s; retrying",
                    attempt,
                    url,
                    exc,
                )
                continue
            raise
    # Unreachable: the loop either returns or re-raises above. Kept so
    # mypy sees a total function.
    raise RuntimeError(  # pragma: no cover
        f"Phenom fetch loop exited without returning ({last_exc!r})"
    )


class PhenomStrategy:
    """Phenom People ``refineSearch`` API adapter."""

    name: ClassVar[str] = "phenom"

    async def extract(self, company: Company, ctx: RunContext) -> dict[str, Any]:
        """Fetch the tenant's region-filtered jobs and synthesize URLs.

        Never raises out — every failure mode (missing config, HTTP,
        JSON, unexpected payload shape) is folded into an error report
        so the CLI's per-company loop stays intact.
        """
        start = time.time()
        error: str | None = None
        jobs: list[str] = []

        try:
            config = company.phenom
            if config is None:
                # Belt to the schema validator's braces (the
                # ``board_token`` precedent): a hand-mutated Company or
                # one constructed outside the catalog could reach here.
                raise ValueError(
                    f"strategy='phenom' requires a PhenomConfig on "
                    f"company={company.name!r}; got phenom=None."
                )

            path_prefix = company.link_rule.path_prefix
            if not path_prefix:
                raise ValueError(
                    f"strategy='phenom' requires link_rule.path_prefix on "
                    f"company={company.name!r} — posting URLs are "
                    f"synthesized from it and Phenom payloads carry no "
                    f"board URL to fall back on. BCG's value is "
                    f"'/global/en/job'."
                )

            parsed = urlparse(company.job_board_url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            url = f"{origin}{config.endpoint_path}"

            # The region's canonical country name is the server-side
            # facet value; see the module docstring for why the rest of
            # ``filter_tokens`` is not usable here.
            country_value = ctx.region.filter_tokens[0]
            body = build_request_body(config, country_value)

            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                payload = await _fetch_refine_search(client, url, body)

            refine = payload.get("refineSearch")
            if not isinstance(refine, dict):
                raise ValueError(
                    f"Phenom response for company={company.name!r} is missing "
                    f"the 'refineSearch' key (keys={sorted(payload)!r})."
                )
            data = refine.get("data")
            if not isinstance(data, dict):
                raise ValueError(
                    f"Phenom response for company={company.name!r} is missing "
                    f"'refineSearch.data' (keys={sorted(refine)!r})."
                )
            raw_jobs = data.get("jobs")
            if not isinstance(raw_jobs, list):
                raise ValueError(
                    f"Phenom response for company={company.name!r} has "
                    f"'refineSearch.data.jobs' of type "
                    f"{type(raw_jobs).__name__}, expected list."
                )

            total_hits = refine.get("totalHits")
            if isinstance(total_hits, int) and total_hits > len(raw_jobs):
                # Single-call contract exceeded. Emit what we have; the
                # verdict layer classifies the shortfall.
                logger.warning(
                    "Phenom reported totalHits=%s but returned %s records for "
                    "company=%s (size=%s). Emitting the received subset; the "
                    "verdict layer will classify the shortfall.",
                    total_hits,
                    len(raw_jobs),
                    company.name,
                    _PAGE_SIZE,
                )

            seen: set[str] = set()
            for job in raw_jobs:
                if not isinstance(job, dict):
                    continue
                if not _record_matches_region(job, ctx.region):
                    continue
                job_id = str(job.get("jobId") or "").strip()
                title = str(job.get("title") or "")
                if not job_id or not slugify_title(title):
                    # Without both halves the synthesized URL would be
                    # malformed; skipping is more honest than emitting
                    # a link that 404s.
                    logger.warning(
                        "Phenom record for company=%s lacks a usable jobId/title "
                        "(jobId=%r, title=%r); skipping.",
                        company.name,
                        job.get("jobId"),
                        job.get("title"),
                    )
                    continue
                job_url = synthesize_job_url(origin, path_prefix, job_id, title)
                if job_url not in seen:
                    seen.add(job_url)
                    jobs.append(job_url)

        except Exception as exc:  # noqa: BLE001 — deliberate catch-all
            logger.exception("PhenomStrategy failed for company=%s", company.name)
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
