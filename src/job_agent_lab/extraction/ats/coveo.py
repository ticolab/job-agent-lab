"""Coveo search-API adapter (SYS-18).

Fifth extraction strategy through the port (after ``dom``,
``greenhouse``, ``phenom``, ``talentbrew``) and the last of the three
R2 API adapters. Coveo is a search *platform* rather than an ATS, so
the request shape is standardised across tenants: ``POST
https://{organization_id}.org.coveo.com/rest/search/v2`` answers with
``{totalCount, results: [...], ...}`` where each result carries a
ready-made ``clickUri``. This adapter mints an anonymous bearer token,
issues that POST with the region facet selected, re-verifies each
record's location client-side, and emits ``clickUri`` verbatim. No
rendered page, no DOM, no LLM — which is why the report's
``metadata.model`` / ``agent_steps`` / ``agent_completed`` /
``agent_had_errors`` are all ``None`` (rendered as ``n/a`` by
``print_summary``).

Why an API adapter — the C1 half that closes without click-simulation
--------------------------------------------------------------------
UST's board (``blockers/INTEGRATION_BLOCKERS_R2.md``) is a React SPA
that renders **zero** ``<a href>`` job anchors at any viewport: the
probe reports matcher count ``0`` under both the derived prefix
``/en`` and the ancestor sweep ``/``, across two independent
investigations. No matcher extension reaches them, because the
destination URLs never enter the DOM — they exist only inside the
Coveo search XHR's ``clickUri`` fields, which the SPA consumes to
build click handlers. That is the C1 shape at its purest, and unlike
the click-handler-only boards it closes cleanly: the XHR that the page
itself calls is replayable, auth-free-by-design, and carries better
data than the DOM ever would.

The auth story — a tenant-owned mint path
-----------------------------------------
The formerly-open auth question is pinned by the P1 capture
(``spike/evidence/ust_coveo_token.NOTES.txt``). There is **no**
Coveo-platform-hosted anonymous-token endpoint to call. The tenant's
own front door proxies Coveo's ``/rest/search/token`` and answers
anonymously: ``GET https://www.ust.com/services/search`` returns
``{"token": "<JWT>"}`` — HS256, ``roles: ["queryExecutor"]``, an
anonymous user id, a 24-hour ``exp``, and no cookie or CSRF binding
whatsoever. Two consequences shape this module:

* ``token_url`` is **per-tenant config**, not a platform constant.
  UST alone documents a second same-host variant
  (``/content/global/us/en/_jcr_content.token.json``) that its page
  falls back to on content-authoring paths, so per-tenant path
  variance is guaranteed rather than hypothetical.
* The ``currentDate`` query parameter the browser sends is a
  client-side ``Date.now()`` cache-buster that the notes pin as *not
  consumed server-side* (and the response is ``cache-control:
  no-store`` regardless), so this adapter omits it. Sending it would
  imply a contract that does not exist. Re-verified on 2026-08-04: an
  in-page ``fetch`` of the mint path behaves identically with and
  without the parameter, so it is not a gate.

**The httpx mint path is unreachable on gated tenants; SYS-19 borrows
instead (2026-08-15).** ``www.ust.com`` moved behind Cloudflare Bot
Management after the 2026-07-22 capture and answers **HTTP 403** to
every :mod:`httpx` request — a plain ``GET`` of the careers page
included, and the captured curl's full browser-header set included. The
block is on the *client*, not the request shape. That is C19, and it
defeats any HTTP client regardless of headers, so no amount of header
work reopens it.

What does work is not minting at all. A real Chromium (SYS-10 plausible
UA) loads the page ``200``, its own XHR mints normally, and the JWT
lands in ``sessionStorage['searchToken_en_us']``; reading that stored
value is not a request and is not gated.
:func:`~job_agent_lab.extraction.ats.browser_token.read_session_storage_token`
does exactly that, selected by
:attr:`CoveoConfig.browser_token_key`, and the search half then runs
over ``httpx`` as before because Coveo's platform host is a **different
origin and is not gated**. Verified end to end 2026-08-15: borrowed
token → ``200`` → ``totalCount: 20`` for UST's Costa Rica facet.

The distinction is narrow and must be preserved. Re-issuing the mint
request from inside the loaded page via ``page.evaluate`` returns
``403`` even though the page's own XHR to the same URL had just
succeeded — so "use a browser" is not the fix; "read what the page
already stored" is. The rejected alternative was a pre-minted token in
config: JWTs expire, so it decays into recurring manual maintenance,
whereas a borrowed token is minted fresh by the page on every run.

**One mint per run, and no renewal in v1.** The 24-hour TTL dwarfs any
run duration, so re-minting mid-run would be dead code guarding a case
that cannot occur. A search ``401`` is therefore a hard error rather
than a re-mint trigger. The design that would replace this — the
tenant page's own ``renewAccessToken`` callback registered with
Coveo's ``SearchEndpoint`` — is recorded in the P1 notes and is the
documented stretch goal; it should arrive with the evidence of a run
long enough to need it.

``clickUri`` is emitted verbatim — never reconstructed
-----------------------------------------------------
This is the module's sharpest rule and it carries a standing warning
from the round-1 investigation, quoted in the ledger: "any matcher
extension that operated on the Coveo response would need to preserve
``clickUri`` verbatim rather than reconstruct a URL from
``raw.jobid``." The recorded payload shows exactly why. Every one of
the ten captured Costa Rica records has ``raw.jobid`` **disagreeing**
with its ``clickUri`` tail — ``clickUri``
``https://www.ust.com/jobs/80708213`` against ``raw.jobid=48689``, and
the same mismatch on all ten. The two ids belong to different
schemes (``raw.jobid`` is the ATS-side identifier, the ``clickUri``
tail is a site-side ``apientityid``), and the ledger records that the
old ``#jobid=<n>`` fragment form has been retired page-side entirely.
Reconstructing from ``raw.jobid`` would therefore emit ten URLs that
do not resolve. A record whose ``clickUri`` is missing or empty is
**skipped with a warning** rather than patched from another field —
skipping is honest, synthesis is the trap.

Region filtering is belt-and-braces (the Greenhouse / Phenom pattern):
the request asks the server for the region via a selected facet, *and*
every returned record is re-verified client-side by
:func:`_record_matches_region`. The facet value sent to the server is
``region.filter_tokens[0]`` — the region's canonical country name,
byte-equal to the captured facet value — deliberately not the whole
preferred-half slice, which also contains ``"CR"``, a DOM-filter token
the facet has no value for.

**``raw.city`` is list-typed.** The recorded payload carries
``raw.country: "Costa Rica"`` (a string) alongside ``raw.city:
["Heredia"]`` (a list), so the re-verification predicate coerces
``str | list | None`` and tests each entry independently. See
:func:`_record_matches_region`.

Pagination
----------
``numberOfResults: 100`` covers UST's Costa Rica subset (20) in a
single call, but ``firstResult`` paging is implemented rather than
deferred, because the contract for it is unambiguous and the
alternative is an adapter that silently truncates the first tenant
whose region exceeds the page size. The loop advances while the
response's ``totalCount`` exceeds the number of records seen so far
*and* the last page returned at least one record; a defensive
``_MAX_PAGES`` cap mirrors the SYS-5 walker. Ending short of
``totalCount`` logs a warning naming the shortfall and emits what was
collected — the SYS-9 verdict layer then flips ``under``, which is its
job. Note ``totalCount`` counts the server's facet-filtered set, so it
is the right paging bound but *not* the expected emit count: the
client-side re-verification can legitimately drop records below it.

Failure semantics
-----------------
- Token mint transport error or 5xx: one retry, then a hard error
  report (``metadata.error`` populated, ``jobs=[]``). A run that
  cannot authenticate has nothing honest to report.
- Token response missing a non-empty string ``token``: loud error.
- Search transport error or 5xx: one retry, then error report.
- Search ``401``/``403``: hard error, no re-mint (see the auth section).
- 200 with a body missing ``results`` (or of the wrong type): loud
  error — a contract breakage, not an empty board.
- **200 with zero region matches: honest empty result, not an error**
  (``metadata.error`` is ``None``, ``jobs`` is ``[]``). A tenant with
  no Costa Rica postings today differs from a broken board.
- Missing ``coveo`` config: error report (a schema fault surfaced
  without raising, mirroring the sibling adapters).
- Any other exception: caught and folded into an error report —
  :meth:`CoveoStrategy.extract` never raises out, so the CLI's
  per-company loop survives.

Live verification status (2026-08-04)
------------------------------------
The search half of the contract is verified against the live endpoint
with a browser-borrowed token; the mint half is blocked by the
Cloudflare gate above. What the live call established:

- ``totalCount: 20`` and 20 records returned in a single call, matching
  the ledger's human-counted ``expected_jobs: 20``.
- **The trimmed body works, and ``fieldsToInclude`` is load-bearing**:
  zero of the 20 records were missing ``raw.country`` or ``raw.city``.
  This was the open contract risk — Coveo scopes ``raw.*`` to a default
  set when the key is omitted, which would have silently starved the
  re-verification predicate.
- ``raw.city`` is list-typed on every record (``["Heredia"]``).
- ``raw.jobid`` disagrees with the ``clickUri`` tail on **20 of 20**
  records, so the verbatim-emission rule is validated at full scale
  rather than inferred from the 10-record browser capture.
- All 20 records pass client-side re-verification, so the belt agrees
  with the facet's braces on this tenant.

Fixture policy
--------------
A Coveo company deliberately has **no DOM snapshot** — there is no
anchor to freeze. Its regression artifact is a recorded payload at
``tests/fixtures/api/coveo/<handle>.json``, which is the UST half of
C1's closure. The committed payload records *this adapter's* contract
(20 records, captured through :func:`build_search_body` on 2026-08-04)
rather than the browser's 10-record page-size capture in
``spike/evidence/`` — replaying the latter would pin a contract the
adapter never issues. Two hygiene rules attach: the search fixture is
scrubbed of ``indexToken`` / ``searchUid``, and the token fixture
carries a **synthetic** value — the captured JWT must never land in
git, expiry notwithstanding.
"""

from __future__ import annotations

import logging
import time
from typing import Any, ClassVar

import httpx

from job_agent_lab.domain.company import Company, CoveoConfig
from job_agent_lab.domain.region import TargetRegion
from job_agent_lab.extraction.base import RunContext, build_report

logger = logging.getLogger(__name__)

# Total wall-clock budget for a single HTTP call (connect + read).
# Mirrors the Phenom / Talentbrew / Greenhouse adapters; UST's ~129 KiB
# filtered payload lands well inside it.
_REQUEST_TIMEOUT = httpx.Timeout(30.0)

# Records requested per search call. UST's Costa Rica facet returns 20
# against this ceiling, so the live board never pages; the loop exists
# for the first tenant whose region does not fit. See the module
# docstring's pagination section.
_PAGE_SIZE = 100

# Defensive cap on the pagination loop, mirroring the SYS-5 walker's
# ``MAX_PAGES``. At ``_PAGE_SIZE`` records per call this bounds a run
# at 1,000 records — far above any region-filtered facet observed, and
# a loud warning fires if it is ever reached.
_MAX_PAGES = 10


def build_search_body(
    config: CoveoConfig, country_value: str, first_result: int = 0
) -> dict[str, Any]:
    """Build the ``/rest/search/v2`` POST body for one tenant + region.

    This is the single expression of the wire contract — the adapter
    holds no other request-shaping logic. The field set is the trimmed
    template from ``ARCHITECTURE_PROPOSAL_R2.md`` §4.8.3, which
    replicates the captured working contract minus its UI-state noise.
    Three properties are load-bearing:

    * **No ``aq``.** Filtering is entirely facet-state-based: the
      country facet carries the target value with ``state:
      "selected"`` and every other facet is omitted. The captured
      browser request likewise carries no ``aq``, so adding an
      advanced-query expression here would diverge from the contract
      that is known to work.
    * **``fieldsToInclude`` is sent explicitly.** The captured request
      lists 24 fields; this template asks for the three the adapter
      actually reads (``city``, ``country``, ``jobid``). Coveo scopes
      ``raw.*`` to a default field set when the key is absent, and the
      client-side re-verification depends on ``raw.country`` /
      ``raw.city`` being present, so omitting it would risk silently
      dropping every record. ``jobid`` is requested not because the
      adapter emits it — it must never do that, see the module
      docstring — but so the dual-scheme mismatch stays observable in
      recorded payloads.
    * **The facet object is minimal.** ``field`` / ``facetId`` /
      ``type`` / ``currentValues`` are what the server needs; the
      captured object's ``injectionDepth``, ``numberOfValues``,
      ``sortCriteria``, ``freezeCurrentValues``, ``preventAutoSelect``,
      ``delimitingCharacter``, ``filterFacetCount`` and
      ``isFieldExpanded`` are widget bookkeeping, as are the ~100
      sibling ``state: "idle"`` values the browser echoes back.

    Args:
        config: The tenant's :class:`CoveoConfig` (supplies
            ``search_hub``).
        country_value: The server-side facet value, i.e. the region's
            canonical country name.
        first_result: Zero-based offset of the first record to return.
            The pagination loop advances this by :data:`_PAGE_SIZE`.

    Returns:
        A JSON-serialisable dict ready for ``client.post(json=...)``.
    """
    return {
        "q": "",
        "tab": "default",
        "locale": "en-US",
        "searchHub": config.search_hub,
        "sortCriteria": "relevancy",
        "firstResult": first_result,
        "numberOfResults": _PAGE_SIZE,
        "fieldsToInclude": ["city", "country", "jobid"],
        "facets": [
            {
                "facetId": "country",
                "field": "country",
                "type": "specific",
                "currentValues": [{"value": country_value, "state": "selected"}],
            }
        ],
    }


def _city_entries(raw_city: object) -> list[str]:
    """Normalise Coveo's ``raw.city`` into a list of strings.

    The field is **list-typed** in the recorded UST payload
    (``["Heredia"]``), but Coveo's raw fields are index-defined and a
    single-valued index would serve a bare string, so both shapes are
    accepted. Anything else (numbers, dicts, ``None``) yields no
    entries rather than raising — a malformed location field must not
    take down a run whose other records are fine.
    """
    if isinstance(raw_city, str):
        return [raw_city] if raw_city else []
    if isinstance(raw_city, list):
        return [entry for entry in raw_city if isinstance(entry, str) and entry]
    return []


def _record_matches_region(result: dict[str, Any], region: TargetRegion) -> bool:
    """Re-verify one record's location against ``region``, client-side.

    Belt to the selected-facet's braces, matching the Greenhouse and
    Phenom adapters. A record matches when its ``raw.country`` alone
    matches, or when any ``country + city`` composition does — the
    latter catches an index whose country field is empty but whose city
    is unambiguously in-region.

    ``raw.city`` is list-typed on the recorded payload, so each entry
    is tested independently via :func:`_city_entries`; a multi-location
    record matches if *any* of its cities does. Missing fields coerce
    to ``""``, which
    :meth:`~job_agent_lab.domain.region.TargetRegion.matches` already
    treats as a non-match, so a record with no location information at
    all is dropped rather than admitted.
    """
    raw = result.get("raw")
    if not isinstance(raw, dict):
        return False

    country = raw.get("country") or ""
    country = country if isinstance(country, str) else ""
    if country and region.matches(country):
        return True

    for city in _city_entries(raw.get("city")):
        if region.matches(city):
            return True
        combined = f"{country} {city}".strip()
        if combined and region.matches(combined):
            return True
    return False


async def _mint_token(client: httpx.AsyncClient, token_url: str) -> str:
    """GET ``token_url`` and return the minted bearer token.

    One retry on transport errors and first-attempt 5xx, mirroring the
    sibling adapters' ``_fetch_*`` helpers. Kept separate from
    :meth:`CoveoStrategy.extract` so the retry policy lives in one
    documented place and is unit-testable through respx.

    The ``currentDate`` cache-buster the browser appends is
    deliberately omitted — see the module docstring's auth section.

    Raises:
        ValueError: If the response is not a JSON object carrying a
            non-empty string ``token``. A malformed mint response is a
            contract breakage, and continuing with an empty bearer
            would produce a misleading 401 downstream.
    """
    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            response = await client.get(token_url)
            if 500 <= response.status_code < 600 and attempt == 1:
                logger.warning(
                    "Coveo token endpoint returned %s on attempt %s for %s; retrying",
                    response.status_code,
                    attempt,
                    token_url,
                )
                continue
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError(
                    f"Coveo token endpoint returned non-object body (type="
                    f"{type(payload).__name__}) from {token_url}."
                )
            token = payload.get("token")
            if not isinstance(token, str) or not token:
                raise ValueError(
                    f"Coveo token endpoint response from {token_url} is "
                    f"missing a non-empty string 'token' "
                    f"(keys={sorted(payload)!r})."
                )
            return token
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt == 1:
                logger.warning(
                    "Coveo token transport error on attempt %s for %s: %s; retrying",
                    attempt,
                    token_url,
                    exc,
                )
                continue
            raise
    # Unreachable: the loop either returns or re-raises above. Kept so
    # mypy sees a total function.
    raise RuntimeError(  # pragma: no cover
        f"Coveo token mint loop exited without returning ({last_exc!r})"
    )


async def _fetch_search_page(
    client: httpx.AsyncClient, url: str, token: str, body: dict[str, Any]
) -> dict[str, Any]:
    """POST ``body`` to ``url`` with a bearer token; one retry on 5xx.

    Retries transport errors and first-attempt 5xx only. A ``401`` or
    ``403`` falls straight through ``raise_for_status`` without a
    re-mint attempt: v1 mints once per run and the 24-hour TTL makes
    mid-run expiry impossible (module docstring, auth section).
    """
    headers = {"Authorization": f"Bearer {token}"}
    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            response = await client.post(url, json=body, headers=headers)
            if 500 <= response.status_code < 600 and attempt == 1:
                logger.warning(
                    "Coveo search API returned %s on attempt %s for %s; retrying",
                    response.status_code,
                    attempt,
                    url,
                )
                continue
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError(
                    f"Coveo search API returned non-object body (type="
                    f"{type(payload).__name__}) from {url}."
                )
            return payload
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt == 1:
                logger.warning(
                    "Coveo search transport error on attempt %s for %s: %s; retrying",
                    attempt,
                    url,
                    exc,
                )
                continue
            raise
    # Unreachable: see ``_mint_token``.
    raise RuntimeError(  # pragma: no cover
        f"Coveo search fetch loop exited without returning ({last_exc!r})"
    )


def search_endpoint_url(organization_id: str) -> str:
    """Compose the tenant's Coveo search endpoint URL.

    Unlike the sibling adapters' endpoints this is a *platform*
    address, derived entirely from ``organization_id`` rather than from
    ``job_board_url`` — Coveo hosts the search API itself even when the
    token mint is proxied by the tenant. The ``organizationId`` query
    parameter duplicates the host label; the captured request carries
    both and so does this.
    """
    return (
        f"https://{organization_id}.org.coveo.com/rest/search/v2"
        f"?organizationId={organization_id}"
    )


class CoveoStrategy:
    """Coveo ``/rest/search/v2`` API adapter."""

    name: ClassVar[str] = "coveo"

    async def extract(self, company: Company, ctx: RunContext) -> dict[str, Any]:
        """Mint a token, fetch the region-filtered jobs, emit ``clickUri``s.

        Never raises out — every failure mode (missing config, token
        mint, HTTP, JSON, unexpected payload shape) is folded into an
        error report so the CLI's per-company loop stays intact.
        """
        start = time.time()
        error: str | None = None
        jobs: list[str] = []

        try:
            config = company.coveo
            if config is None:
                # Belt to the schema validator's braces (the
                # ``board_token`` precedent): a hand-mutated Company or
                # one constructed outside the catalog could reach here.
                raise ValueError(
                    f"strategy='coveo' requires a CoveoConfig on "
                    f"company={company.name!r}; got coveo=None."
                )

            url = search_endpoint_url(config.organization_id)
            # The region's canonical country name is the server-side
            # facet value; see the module docstring for why the rest of
            # ``filter_tokens`` is not usable here.
            country_value = ctx.region.filter_tokens[0]

            seen: set[str] = set()
            total_count: int | None = None
            records_seen = 0

            # SYS-19: two token sources, exactly one set (the schema
            # validator enforces that). ``browser_token_key`` borrows the
            # JWT the tenant's own page minted into sessionStorage —
            # required where the mint origin is behind bot management and
            # refuses HTTP clients outright (C19). The import is lazy so
            # this module stays importable without the browser stack.
            token: str
            if config.browser_token_key is not None:
                from job_agent_lab.extraction.ats.browser_token import (
                    read_session_storage_token,
                )

                token = await read_session_storage_token(
                    company.job_board_url,
                    config.browser_token_key,
                    headless=ctx.headless,
                )
            elif config.token_url is None:
                # Belt to the schema validator's braces, matching
                # ``greenhouse.board_token``: catches a hand-mutated or
                # model_construct-ed config that bypassed validation.
                raise ValueError(
                    f"CoveoConfig on company={company.name!r} has neither "
                    f"token_url nor browser_token_key set; one is required."
                )

            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                if config.browser_token_key is None:
                    assert config.token_url is not None  # narrowed above
                    token = await _mint_token(client, config.token_url)

                for page in range(1, _MAX_PAGES + 1):
                    body = build_search_body(config, country_value, records_seen)
                    payload = await _fetch_search_page(client, url, token, body)

                    raw_results = payload.get("results")
                    if not isinstance(raw_results, list):
                        raise ValueError(
                            f"Coveo response for company={company.name!r} has "
                            f"'results' of type {type(raw_results).__name__}, "
                            f"expected list."
                        )

                    if total_count is None:
                        total_count = (
                            payload.get("totalCount")
                            if isinstance(payload.get("totalCount"), int)
                            else None
                        )
                        logger.info(
                            "Coveo reported totalCount=%s for company=%s (facet=%r).",
                            total_count,
                            company.name,
                            country_value,
                        )

                    for result in raw_results:
                        if not isinstance(result, dict):
                            continue
                        if not _record_matches_region(result, ctx.region):
                            continue
                        click_uri = result.get("clickUri")
                        if not isinstance(click_uri, str) or not click_uri:
                            # Never synthesize from ``raw.jobid`` — the
                            # two id schemes disagree on every recorded
                            # record. See the module docstring.
                            logger.warning(
                                "Coveo record for company=%s lacks a usable "
                                "clickUri (jobid=%r); skipping rather than "
                                "reconstructing a URL.",
                                company.name,
                                (result.get("raw") or {}).get("jobid")
                                if isinstance(result.get("raw"), dict)
                                else None,
                            )
                            continue
                        if click_uri not in seen:
                            seen.add(click_uri)
                            jobs.append(click_uri)

                    records_seen += len(raw_results)

                    # Terminate on an empty page (nothing more to
                    # fetch) or once the server's own count is
                    # satisfied. ``total_count`` bounds the *facet*
                    # set, not the emitted set — client-side
                    # re-verification may drop records below it.
                    if not raw_results:
                        break
                    if total_count is None or records_seen >= total_count:
                        break
                    if page == _MAX_PAGES:
                        logger.warning(
                            "Coveo pagination hit the _MAX_PAGES=%s cap for "
                            "company=%s after %s records (totalCount=%s). "
                            "Emitting the collected subset; the verdict layer "
                            "will classify the shortfall.",
                            _MAX_PAGES,
                            company.name,
                            records_seen,
                            total_count,
                        )

            if total_count is not None and records_seen < total_count:
                logger.warning(
                    "Coveo reported totalCount=%s but only %s records were "
                    "retrieved for company=%s. Emitting the received subset; "
                    "the verdict layer will classify the shortfall.",
                    total_count,
                    records_seen,
                    company.name,
                )

        except Exception as exc:  # noqa: BLE001 — deliberate catch-all
            logger.exception("CoveoStrategy failed for company=%s", company.name)
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
