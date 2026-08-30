"""BambooHR careers-list API strategy.

Hits the tenant's own ``/careers/list`` endpoint — a public JSON
listing served from the same origin as the human-facing board
(``https://<tenant>.bamboohr.com/careers``) — and returns a report in
the shape authored by
:func:`~job_agent_lab.extraction.base.build_report`. The region filter
runs against the payload's structured location fields via
:meth:`~job_agent_lab.domain.region.TargetRegion.matches` — no rendered
page, no DOM, no LLM — so the four agent-only report fields
(``metadata.model`` / ``agent_steps`` / ``agent_completed`` /
``agent_had_errors``) are all ``None`` and ``print_summary`` renders
each as ``n/a``.

Why an adapter rather than the DOM path. BambooHR's rendered board is
matcher-friendly — anchors are plain ``<a href="/careers/<id>">`` and
two corpus entries (Gorilla Logic, Chainstack) collect from it happily
on ``strategy="dom"``. What the rendered board does *not* offer is any
way to narrow by region: it ships no location filter of any kind, and
the anchor URL is ``/careers/<id>`` with no location component, so the
matcher can only ever return the whole board. Cornelis Networks is the
motivating case — 32 postings of which 7 are Costa Rica — where "the
whole board" and "the region-filtered set" differ by 25 postings that
are mostly United States. The location text exists only in the card
body, which the URL-shaped matcher cannot read. This endpoint carries
it as structured data, which is the entire reason this adapter exists.

Location semantics. Each record carries two location objects and they
disagree about which is populated:

- ``atsLocation`` — ``{country, state, province, city}``, the richer of
  the two and the only one carrying **country**. Populated for 27 of
  Cornelis' 32 records.
- ``location`` — ``{city, state}``, no country. Populated for some
  records where ``atsLocation`` is entirely null.

:func:`location_text` joins whichever fields are present, preferring
``atsLocation`` and falling back to ``location``, into a single
comma-separated string for the region predicate. Fully-remote postings
with no location at all collapse to ``""``, which
:meth:`TargetRegion.matches` rejects (it returns ``False`` on falsy
input rather than raising) — the correct read, since a posting with no
stated location cannot be shown to be in-region.

The country-bearing field mattering is not incidental. On the
motivating board the Costa Rica postings render as city ``San Jose``,
country ``Costa Rica``, while a separate group renders as city
``San Jose``, state ``California``, country ``United States``. A
city-only comparison would conflate the two — the same San Jose
ambiguity that Veeam's Talentbrew board exhibits — so joining the
country in is what keeps them apart.

Failure semantics, mirroring
:mod:`~job_agent_lab.extraction.ats.greenhouse`:

- Transport error or 5xx: one retry, then an error report with
  ``metadata.error`` populated and an empty ``jobs`` list.
- Non-200 after retry (including 404 — most often a retired tenant
  subdomain): error report.
- 200 with a body missing the ``result`` key, or whose ``result`` is
  not a list: error report.
- **200 with zero region matches: honest empty result, not an error**
  (``metadata.error`` is ``None``, ``jobs`` is ``[]``). A tenant with
  no Costa Rica postings today is different from a broken board.
- Any other exception: caught and folded into an error report —
  :meth:`extract` never raises out.
"""

from __future__ import annotations

import logging
import time
from typing import Any, ClassVar
from urllib.parse import urlparse

import httpx

from job_agent_lab.domain.company import Company
from job_agent_lab.domain.region import TargetRegion
from job_agent_lab.extraction.base import RunContext, build_report

logger = logging.getLogger(__name__)

# Path appended to the tenant origin to reach the JSON listing. A
# platform constant, not tenant config — unlike Phenom/Talentbrew/Coveo,
# BambooHR exposes the same path on every tenant subdomain.
_LIST_PATH = "/careers/list"

# Path prefix for synthesised posting URLs. The rendered board links
# each posting as ``/careers/<id>``; we reproduce that exactly so the
# emitted URLs are the ones a human would land on.
_POSTING_PATH = "/careers"

# Total wall-clock budget for a single HTTP call (connect + read).
# Matches the Greenhouse adapter's budget; the payload is small (32
# records / ~13 KiB on the motivating board).
_REQUEST_TIMEOUT = httpx.Timeout(30.0)

# Kept in sync with ``domain.company._BAMBOOHR_HOST_SUFFIX``;
# duplicated here on purpose so :func:`board_origin` is a belt to the
# schema validator's braces, exactly as ``greenhouse.board_token`` is.
_HOST_SUFFIX = ".bamboohr.com"

# Fields joined into the region-predicate string, in output order.
# ``province`` sits between state and country because BambooHR uses it
# as the non-US analogue of ``state``.
_ATS_LOCATION_FIELDS = ("city", "state", "province", "country")
_LOCATION_FIELDS = ("city", "state")


def board_origin(company: Company) -> str:
    """Return the tenant origin (``scheme://host``) for the board.

    The adapter addresses the tenant by origin rather than by an
    extracted token: BambooHR's identity *is* the subdomain, and both
    the list endpoint and the synthesised posting URLs hang off it.

    This helper is the *belt* to the ``Company`` model validator's
    *braces* (see
    :meth:`job_agent_lab.domain.company.Company._validate_bamboohr_host`).
    The validator runs once at instantiation; this runs on every
    :meth:`BambooHrStrategy.extract` call and would catch a
    hand-mutated ``Company`` that bypassed the schema.

    Raises:
        ValueError: If ``company.strategy != "bamboohr"``, or the host
            is not a tenant subdomain of ``bamboohr.com``.
    """
    if company.strategy != "bamboohr":
        raise ValueError(
            f"board_origin expects strategy='bamboohr'; got "
            f"strategy={company.strategy!r} on company={company.name!r}."
        )

    parsed = urlparse(company.job_board_url)
    host = parsed.hostname or ""
    if not host.endswith(_HOST_SUFFIX):
        raise ValueError(
            f"board_origin expects a tenant subdomain of "
            f"{_HOST_SUFFIX.lstrip('.')}; got host={host!r} in "
            f"url={company.job_board_url!r}."
        )
    return f"{parsed.scheme}://{parsed.netloc}"


def location_text(record: dict[str, Any]) -> str:
    """Flatten one record's location objects into a predicate string.

    Prefers ``atsLocation`` (the only object carrying ``country``) and
    falls back to ``location`` when ``atsLocation`` contributes nothing.
    Returns ``""`` when neither is populated, which
    :meth:`TargetRegion.matches` treats as "no match" rather than an
    error.

    Kept public and side-effect free so the field-precedence rule is
    unit-testable without HTTP — the same reason
    ``talentbrew.build_query_params`` is public.
    """
    ats = record.get("atsLocation") or {}
    parts = [str(ats.get(f)).strip() for f in _ATS_LOCATION_FIELDS if ats.get(f)]
    if parts:
        return ", ".join(parts)

    fallback = record.get("location") or {}
    parts = [str(fallback.get(f)).strip() for f in _LOCATION_FIELDS if fallback.get(f)]
    return ", ".join(parts)


def select_region_urls(
    records: list[Any], origin: str, region: TargetRegion
) -> list[str]:
    """Return synthesised posting URLs for records matching ``region``.

    Records that are not objects, or that carry no usable ``id``, are
    skipped rather than raising: a single malformed row on an otherwise
    healthy board should cost one posting, not the whole run. Order
    follows the payload, and duplicate ids are dropped so a tenant
    listing the same opening twice cannot inflate the count.
    """
    urls: list[str] = []
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        job_id = record.get("id")
        if job_id is None:
            continue
        key = str(job_id).strip()
        if not key or key in seen:
            continue
        if not region.matches(location_text(record)):
            continue
        seen.add(key)
        urls.append(f"{origin}{_POSTING_PATH}/{key}")
    return urls


async def _fetch_list_payload(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    """Fetch ``url`` with one retry on transport errors / 5xx.

    Mirrors :func:`greenhouse._fetch_jobs_payload` so the retry policy
    reads identically across the two host-gated adapters.
    """
    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            response = await client.get(url)
            if 500 <= response.status_code < 600 and attempt == 1:
                logger.warning(
                    "BambooHR API returned %s on attempt %s for %s; retrying",
                    response.status_code,
                    attempt,
                    url,
                )
                continue
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError(
                    f"BambooHR API returned non-object body (type="
                    f"{type(payload).__name__}) from {url}."
                )
            return payload
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt == 1:
                logger.warning(
                    "BambooHR API transport error on attempt %s for %s: %s; retrying",
                    attempt,
                    url,
                    exc,
                )
                continue
            raise
    raise RuntimeError(  # pragma: no cover
        f"BambooHR fetch loop exited without returning ({last_exc!r})"
    )


class BambooHrStrategy:
    """BambooHR careers-list API adapter."""

    name: ClassVar[str] = "bamboohr"

    async def extract(self, company: Company, ctx: RunContext) -> dict[str, Any]:
        """Fetch the tenant's listing and filter by ``ctx.region``.

        Never raises out — any exception is caught and folded into an
        error report so the CLI's per-company loop survives.
        """
        start = time.time()
        error: str | None = None
        jobs: list[str] = []

        try:
            origin = board_origin(company)
            url = f"{origin}{_LIST_PATH}"
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                payload = await _fetch_list_payload(client, url)

            records = payload.get("result")
            if records is None:
                raise ValueError(
                    f"BambooHR API response for origin={origin!r} is "
                    f"missing the 'result' key (keys={sorted(payload)!r})."
                )
            if not isinstance(records, list):
                raise ValueError(
                    f"BambooHR API response for origin={origin!r} has "
                    f"'result' of type {type(records).__name__}, expected list."
                )

            jobs = select_region_urls(records, origin, ctx.region)

        except Exception as exc:  # noqa: BLE001 — deliberate catch-all
            logger.exception("BambooHrStrategy failed for company=%s", company.name)
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
