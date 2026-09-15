"""Greenhouse job-boards API strategy.

Hits Greenhouse's public JSON API — the
``https://boards-api.greenhouse.io/v1/boards/<token>/jobs`` endpoint —
and returns a report in the same shape as
:class:`~vacantes.extraction.dom.strategy.DomStrategy`, authored by
:func:`~vacantes.extraction.base.build_report`. The region filter
runs against the API's structured ``location.name`` field via
:meth:`~vacantes.domain.region.TargetRegion.matches` — no
rendered page, no DOM, no LLM — which is why the returned report's
``metadata.model`` / ``agent_steps`` / ``agent_completed`` /
``agent_had_errors`` are all ``None``. ``print_summary`` renders each
as ``n/a``.

Notes on the endpoint choice: ``Company.job_board_url`` for a
``strategy="greenhouse"`` entry is set to the **user-facing HTML board
URL** (``https://job-boards.greenhouse.io/<token>`` or the legacy
``https://boards.greenhouse.io/<token>``); that is the URL a human
lands on and the one the :func:`Company` schema validator gates. The
runtime derives the ``<token>`` via :func:`board_token` and points
``httpx`` at ``boards-api.greenhouse.io``, which is Greenhouse's
public JSON API host and the only one that returns structured job data.
The two hostnames are separate services on purpose — do not confuse
them.

Failure semantics:

- Transport error (``httpx.TransportError`` — DNS, connection refused,
  read timeout) or 5xx: one retry, then an error report with
  ``metadata.error`` populated and an empty ``jobs`` list.
- Non-200 after retry (including 404 — most often a wrong / retired
  ``board_token``): error report.
- 200 with a body missing the ``jobs`` key: error report.
- **200 with zero region matches: honest empty result, not an error**
  (``metadata.error`` is ``None`` and ``jobs`` is ``[]``). A tenant
  with no Costa Rica postings today is different from a tenant whose
  board is broken.
- Any other exception (JSON decode error, unexpected payload shape):
  caught and folded into an error report — :meth:`extract` never
  raises out.
"""

from __future__ import annotations

import logging
import time
from typing import Any, ClassVar
from urllib.parse import urlparse

import httpx

from vacantes.domain.company import Company
from vacantes.extraction.base import RunContext, build_report

logger = logging.getLogger(__name__)

# The public JSON API host. Distinct from the user-facing HTML board
# host (``job-boards.greenhouse.io`` / ``boards.greenhouse.io``) that
# ``Company.job_board_url`` points at.
_API_BASE = "https://boards-api.greenhouse.io/v1/boards"

# Total wall-clock budget for a single HTTP call (connect + read).
# Chosen generous enough for the largest boards seen live (Zscaler at
# ~340 postings / ~330 KiB), but small enough that a wedged connection
# hits the retry path in under a minute total.
_REQUEST_TIMEOUT = httpx.Timeout(30.0)

# Hosts accepted for the user-facing ``Company.job_board_url``. Kept in
# sync with ``domain.company._GREENHOUSE_HOSTS``; duplicated here on
# purpose so :func:`board_token` is a belt to the schema validator's
# braces and can be called on any ``Company`` without re-validating.
_ACCEPTED_HOSTS: frozenset[str] = frozenset(
    {"job-boards.greenhouse.io", "boards.greenhouse.io"}
)


def board_token(company: Company) -> str:
    """Extract the Greenhouse board token from ``company.job_board_url``.

    The board token is the first non-empty path segment of the
    user-facing board URL — e.g. ``westmonroe4`` from
    ``https://job-boards.greenhouse.io/westmonroe4``. It is the
    tenant identifier the JSON API's ``/v1/boards/<token>/jobs``
    endpoint expects.

    This helper is the *belt* to the ``Company`` model validator's
    *braces* (see
    :meth:`vacantes.domain.company.Company._validate_greenhouse_host`).
    The validator runs once at instantiation time; :func:`board_token`
    runs at every :meth:`GreenhouseStrategy.extract` call, and would
    catch a hand-mutated ``Company`` or a mis-classified entry that
    somehow bypassed the schema.

    Raises:
        ValueError: If ``company.strategy != "greenhouse"``, the host
            is not a known Greenhouse host, or the URL path has no
            non-empty first segment.
    """
    if company.strategy != "greenhouse":
        raise ValueError(
            f"board_token expects strategy='greenhouse'; got "
            f"strategy={company.strategy!r} on company={company.name!r}."
        )

    parsed = urlparse(company.job_board_url)
    if parsed.hostname not in _ACCEPTED_HOSTS:
        allowed = ", ".join(sorted(_ACCEPTED_HOSTS))
        raise ValueError(
            f"board_token expects host in ({allowed}); got "
            f"host={parsed.hostname!r} in url={company.job_board_url!r}."
        )

    segments = [s for s in parsed.path.split("/") if s]
    if not segments:
        raise ValueError(
            f"board_token expects a non-empty first path segment in "
            f"url={company.job_board_url!r}; got path={parsed.path!r}."
        )
    return segments[0]


async def _fetch_jobs_payload(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    """Fetch ``url`` with one retry on transport errors / 5xx.

    Kept as a private helper so :meth:`GreenhouseStrategy.extract`
    stays focused on report shaping; the retry policy is documented
    in one place and unit-tested through respx.
    """
    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            response = await client.get(url)
            if 500 <= response.status_code < 600 and attempt == 1:
                # First-attempt 5xx: log and retry. On attempt 2 we
                # fall through to ``raise_for_status`` below, which
                # promotes the second 5xx to an ``HTTPStatusError``.
                logger.warning(
                    "Greenhouse API returned %s on attempt %s for %s; retrying",
                    response.status_code,
                    attempt,
                    url,
                )
                continue
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError(
                    f"Greenhouse API returned non-object body (type="
                    f"{type(payload).__name__}) from {url}."
                )
            return payload
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt == 1:
                logger.warning(
                    "Greenhouse API transport error on attempt %s for %s: %s; retrying",
                    attempt,
                    url,
                    exc,
                )
                continue
            raise
    # Unreachable: the retry loop either returns or re-raises above.
    # Kept as a defensive assertion so mypy sees a total function.
    raise RuntimeError(  # pragma: no cover
        f"Greenhouse fetch loop exited without returning ({last_exc!r})"
    )


class GreenhouseStrategy:
    """Greenhouse job-boards API adapter."""

    name: ClassVar[str] = "greenhouse"

    async def extract(self, company: Company, ctx: RunContext) -> dict[str, Any]:
        """Fetch the board's jobs and filter by ``ctx.region``.

        Never raises out — any exception (schema, HTTP, JSON, missing
        key, region-predicate error) is caught and folded into an
        error report so the CLI's per-company loop stays intact for
        the remaining companies.
        """
        start = time.time()
        error: str | None = None
        jobs: list[str] = []

        try:
            token = board_token(company)
            url = f"{_API_BASE}/{token}/jobs"
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                payload = await _fetch_jobs_payload(client, url)

            raw_jobs = payload.get("jobs")
            if raw_jobs is None:
                raise ValueError(
                    f"Greenhouse API response for token={token!r} is "
                    f"missing the 'jobs' key (keys={sorted(payload)!r})."
                )
            if not isinstance(raw_jobs, list):
                raise ValueError(
                    f"Greenhouse API response for token={token!r} has "
                    f"'jobs' of type {type(raw_jobs).__name__}, expected list."
                )

            for job in raw_jobs:
                location = (job.get("location") or {}).get("name") or ""
                if ctx.region.matches(location):
                    absolute_url = job.get("absolute_url")
                    if absolute_url:
                        jobs.append(absolute_url)

        except Exception as exc:  # noqa: BLE001 — deliberate catch-all
            logger.exception("GreenhouseStrategy failed for company=%s", company.name)
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
