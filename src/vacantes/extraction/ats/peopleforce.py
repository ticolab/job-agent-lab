"""PeopleForce careers-board strategy with per-run location discovery.

PeopleForce hosts every tenant on its own subdomain of the platform
domain (``<tenant>.peopleforce.io``) and server-renders the whole
careers board — listing, pagination, and the filter widgets' full
option lists — into the initial HTML. That makes the board reachable
with plain HTTP: this adapter issues two ``GET`` requests, parses
anchors out of the returned HTML with the stdlib parser, applies the
company's :class:`LinkRule` in Python, and reports through
:func:`~vacantes.extraction.base.build_report`. No rendered page,
no DOM, no LLM, which is why the report's ``metadata.model`` /
``agent_steps`` / ``agent_completed`` / ``agent_had_errors`` are all
``None`` (rendered as ``n/a`` by ``print_summary``).

Why this adapter exists — discovery, not extraction
---------------------------------------------------
The DOM path already extracts this board correctly. What it cannot do
is stay *complete*.

PeopleForce persists exactly one location per posting and its filter
widget is single-select, so a region that spans several cities has no
single URL under the widget's own semantics — the shape recorded as
C18 in ``blockers/INTEGRATION_BLOCKERS.md``. The first closure for
that was ``Company.pre_filter_urls``: declare one URL per city and
union the results. It is correct on the day it is written and silently
incomplete afterwards, because the declared set is frozen while the
tenant's location list is not. A posting opened in a city that is not
declared is missed, and — the part that makes it dangerous — the
verdict layer cannot see it: the union still equals the frozen
``expected_jobs``, so the run reports ``match`` while under-counting.
Only the inverse (a declared id that stops resolving) shows up, as
``under``.

This adapter removes the frozen set. Each run reads the board's own
location list and keeps every option whose label the target region
matches, so a newly-added city joins the query the first time it
appears with no config change. On Plan A Technologies today that
selects ``Costa Rica/Cartago``, ``Costa Rica/Heredia``,
``Costa Rica/San José``, and the tenant's ``Latin America`` bucket.

Two platform facts make it work, both live-verified 2026-08-07:

- **The endpoint unions location ids even though the widget does
  not.** PeopleForce is a Rails application and accepts repeated
  ``location_id[]`` parameters, so the whole region is one request
  rather than one request per city. Percent-encoded ``%5B%5D`` is
  accepted identically.
- **Region selection needs no new logic.**
  :meth:`~vacantes.domain.region.TargetRegion.matches` already
  returns ``True`` for ``"Costa Rica/Cartago"`` and ``"Latin America"``
  and ``False`` for ``"Colombia/Bogota"``, ``"United States"``, and
  ``"India"``, because option labels are ordinary location strings.

Request contract
----------------
``GET {job_board_url}`` for discovery, then
``GET {job_board_url}?location_id[]=<id>…[&page=N]`` for results. No
authentication, no cookie, no CSRF token. Only the tenant subdomain
varies, so — unlike the tenant-co-hosted adapters — there is no config
object: ``strategy="peopleforce"`` is gated by a host validator on
:class:`~vacantes.domain.company.Company` instead, following the
Greenhouse precedent.

Anchor filtering reuses :func:`parse_anchor_hrefs` and
:func:`apply_link_rule` from the Talentbrew adapter rather than
re-deriving them. Those are the parity-tested Python mirror of the JS
matcher's URL semantics, so this adapter inherits that guarantee for
free and any future URL-layer change to the matcher fails the parity
test for both adapters at once.

Failure semantics
-----------------
- Transport error or 5xx: one retry, then an error report.
- Non-200 after retry, or a discovery page with **no location options
  at all**: error report. Zero parsed options means the markup moved,
  which is a broken contract rather than an empty region.
- **Options parsed but none match the region: honest empty result**
  (``metadata.error`` is ``None``, ``jobs`` is ``[]``). A tenant that
  does not hire in the region today is not a broken board.
- Any other exception is caught and folded into an error report —
  :meth:`PeopleForceStrategy.extract` never raises out, so the CLI's
  per-company loop survives.
"""

from __future__ import annotations

import logging
import time
from html.parser import HTMLParser
from typing import Any, ClassVar
from urllib.parse import urlencode, urljoin, urlparse, urlsplit, urlunsplit

import httpx

from vacantes.domain.company import Company
from vacantes.domain.region import TargetRegion
from vacantes.extraction.ats.talentbrew import (
    apply_link_rule,
    parse_anchor_hrefs,
)
from vacantes.extraction.base import RunContext, build_report
from vacantes.extraction.dom.rules import derive_path_prefix

logger = logging.getLogger(__name__)

# Total wall-clock budget for a single HTTP call (connect + read).
# Mirrors the sibling adapters' constant.
_REQUEST_TIMEOUT = httpx.Timeout(30.0)

# PeopleForce serves every tenant from a subdomain of this platform
# domain, which is what lets this strategy be gated by a host check
# instead of a per-tenant config object.
PEOPLEFORCE_HOST_SUFFIX = ".peopleforce.io"

# The board paginates ten postings per page. A region-filtered view is
# far smaller in practice, so this cap only exists so a pathological
# response cannot spin the loop; hitting it logs loudly and emits what
# was collected, leaving the shortfall to the verdict layer.
_MAX_PAGES = 20

# PeopleForce marks each filter option with a field-scoped automation
# id. Matching on it keeps the location options separate from the
# employment-type options, which carry identical ``data-value`` /
# ``data-text`` attributes and would otherwise be indistinguishable.
_LOCATION_OPTION_CY_PREFIX = "_location_id_select_"
_LOCATION_OPTION_CY_SUFFIX = "_option"


class _LocationOptionCollector(HTMLParser):
    """Collect ``(id, label)`` for every location option in the board.

    Scoped by ``data-cy`` rather than by ``data-value`` alone: the
    board renders its employment-type filter with the same attribute
    pair, and on Plan A that is two extra options (``Regular``,
    ``Part-time``) among 192. Neither label matches a region today, so
    the scope is belt-and-braces — but it costs nothing and keeps the
    adapter from depending on that accident.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.options: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k: (v or "") for k, v in attrs}
        cy = attr.get("data-cy", "")
        if not (
            cy.startswith(_LOCATION_OPTION_CY_PREFIX)
            and cy.endswith(_LOCATION_OPTION_CY_SUFFIX)
        ):
            return
        value = attr.get("data-value", "").strip()
        label = attr.get("data-text", "").strip()
        if value and label:
            self.options.append((value, label))


def parse_location_options(html: str) -> list[tuple[str, str]]:
    """Return every ``(location_id, label)`` offered by the board.

    Order is document order and duplicates are preserved; selection and
    dedup happen in :func:`select_region_locations`.
    """
    collector = _LocationOptionCollector()
    collector.feed(html)
    return collector.options


def select_region_locations(
    options: list[tuple[str, str]], region: TargetRegion
) -> list[tuple[str, str]]:
    """Keep the options whose label falls inside ``region``.

    Labels are ordinary location strings (``"Costa Rica/Cartago"``,
    ``"Latin America"``, ``"Colombia/Bogota"``), so the region's own
    predicate does the whole job and no PeopleForce-specific parsing
    of the ``country/city`` shape is needed — which also means a
    tenant that writes its labels differently still works.

    Deduplicates by id while preserving first-seen order, so the
    generated query is stable run to run for an unchanged board.
    """
    seen: set[str] = set()
    kept: list[tuple[str, str]] = []
    for value, label in options:
        if value in seen or not region.matches(label):
            continue
        seen.add(value)
        kept.append((value, label))
    return kept


def build_filtered_url(board_url: str, location_ids: list[str], page: int) -> str:
    """Compose the region-filtered results URL for one page.

    Repeated ``location_id[]`` parameters are the whole reason this
    adapter issues one request instead of one per city; see the module
    docstring. Any query string already on ``board_url`` is dropped —
    the filter this adapter builds is the entire filter state, and
    inheriting a stale one would silently narrow the result.
    """
    parts = urlsplit(board_url)
    params: list[tuple[str, str]] = [("location_id[]", i) for i in location_ids]
    if page > 1:
        params.append(("page", str(page)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), ""))


async def _fetch(client: httpx.AsyncClient, url: str) -> str:
    """GET ``url`` with one retry on transport errors / 5xx.

    Kept separate from :meth:`PeopleForceStrategy.extract` so the retry
    policy lives in one documented, unit-testable place — the same
    split the sibling adapters use.
    """
    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            response = await client.get(url)
            if 500 <= response.status_code < 600 and attempt == 1:
                logger.warning(
                    "PeopleForce returned %s on attempt %s for %s; retrying",
                    response.status_code,
                    attempt,
                    url,
                )
                continue
            response.raise_for_status()
            # Bound explicitly: the pre-commit mypy hook runs without
            # httpx installed, so ``response.text`` is ``Any`` there
            # and a bare return trips ``warn_return_any``.
            body: str = response.text
            return body
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt == 1:
                logger.warning(
                    "PeopleForce transport error on attempt %s for %s: %s; retrying",
                    attempt,
                    url,
                    exc,
                )
                continue
            raise
    raise RuntimeError(  # pragma: no cover - loop returns or raises above
        f"PeopleForce fetch loop exited without returning ({last_exc!r})"
    )


class PeopleForceStrategy:
    """PeopleForce careers-board adapter with per-run location discovery."""

    name: ClassVar[str] = "peopleforce"

    async def extract(self, company: Company, ctx: RunContext) -> dict[str, Any]:
        """Discover the region's locations, then fetch their union.

        Never raises out — every failure mode is folded into an error
        report so the CLI's per-company loop stays intact.
        """
        start = time.time()
        error: str | None = None
        jobs: list[str] = []
        locations: list[tuple[str, str]] = []

        try:
            parsed = urlparse(company.job_board_url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            path_prefix = company.link_rule.path_prefix or derive_path_prefix(
                company.sample_job_url
            )

            async with httpx.AsyncClient(
                timeout=_REQUEST_TIMEOUT, follow_redirects=True
            ) as client:
                board_html = await _fetch(client, company.job_board_url)

                options = parse_location_options(board_html)
                if not options:
                    raise ValueError(
                        f"PeopleForce board for company={company.name!r} exposed "
                        f"no location options at {company.job_board_url!r}. The "
                        f"filter markup this adapter reads "
                        f"(data-cy='{_LOCATION_OPTION_CY_PREFIX}<id>"
                        f"{_LOCATION_OPTION_CY_SUFFIX}') has moved or the page "
                        f"did not render."
                    )

                locations = select_region_locations(options, ctx.region)
                logger.info(
                    "PeopleForce discovered %s/%s location options in region %s "
                    "for company=%s: %s",
                    len(locations),
                    len(options),
                    ctx.region.name,
                    company.name,
                    ", ".join(f"{label} ({lid})" for lid, label in locations),
                )
                if not locations:
                    # Honest empty: the board offers locations, none of
                    # them is in the target region.
                    logger.warning(
                        "PeopleForce board for company=%s offers %s locations, "
                        "none matching region %s; emitting an empty result.",
                        company.name,
                        len(options),
                        ctx.region.name,
                    )
                else:
                    jobs = await self._collect(
                        client,
                        company=company,
                        origin=origin,
                        path_prefix=path_prefix,
                        location_ids=[lid for lid, _ in locations],
                    )

        except Exception as exc:  # noqa: BLE001 — deliberate catch-all
            logger.exception("PeopleForceStrategy failed for company=%s", company.name)
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

    async def _collect(
        self,
        client: httpx.AsyncClient,
        *,
        company: Company,
        origin: str,
        path_prefix: str,
        location_ids: list[str],
    ) -> list[str]:
        """Page through the filtered view, unioning LinkRule matches.

        Terminates when a page yields no anchor the previous pages did
        not already have — which covers both the natural last page and
        a board that ignores the ``page`` parameter, since the latter
        re-serves page 1 and therefore adds nothing new.
        """
        seen: set[str] = set()
        ordered: list[str] = []
        for page in range(1, _MAX_PAGES + 1):
            url = build_filtered_url(company.job_board_url, location_ids, page)
            html = await _fetch(client, url)
            absolutized = [urljoin(url, href) for href in parse_anchor_hrefs(html)]
            filtered = apply_link_rule(
                absolutized,
                origin=origin,
                base_path=path_prefix,
                min_depth=company.link_rule.min_depth,
            )
            fresh = [u for u in filtered if u not in seen]
            if not fresh:
                break
            seen.update(fresh)
            ordered.extend(fresh)
        else:
            logger.warning(
                "PeopleForce hit the %s-page cap for company=%s; emitting the "
                "%s postings collected so far.",
                _MAX_PAGES,
                company.name,
                len(ordered),
            )
        return ordered
