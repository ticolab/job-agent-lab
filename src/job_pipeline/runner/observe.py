"""Fold discovery snapshots into durable company-scoped pipeline jobs."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from job_discovery.domain.company import Company
from job_discovery.persistence import jobs_repo as discovery_jobs_repo
from job_discovery.persistence.timestamps import utc_now
from job_pipeline.identity import url_key
from job_pipeline.persistence import jobs_repo

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CompanyObservation:
    company_slug: str
    raw_urls: int
    snapshot_jobs: int
    new: int
    reseen: int
    malformed: int = 0

    @property
    def collapsed(self) -> int:
        return self.raw_urls - self.malformed - self.snapshot_jobs


@dataclass(frozen=True, slots=True)
class ObservationSummary:
    companies: tuple[CompanyObservation, ...]
    snapshot_keys: frozenset[tuple[str, str]]

    @property
    def new(self) -> int:
        return sum(company.new for company in self.companies)

    @property
    def reseen(self) -> int:
        return sum(company.reseen for company in self.companies)

    @property
    def collapsed(self) -> int:
        return sum(company.collapsed for company in self.companies)

    @property
    def malformed(self) -> int:
        return sum(company.malformed for company in self.companies)


def keyed_urls(
    company_slug: str, urls: Sequence[str]
) -> tuple[tuple[tuple[str, str], ...], int]:
    """Pair each URL with its identity key, skipping ones that have none.

    Discovery's matcher only stores absolute same-origin links, so a URL
    that :func:`url_key` rejects is a defect in one board's data. It is
    logged and skipped rather than allowed to abort the observation of
    every other company before any job is processed.
    """
    pairs: list[tuple[str, str]] = []
    malformed = 0
    for url in urls:
        try:
            pairs.append((url_key(url), url))
        except ValueError as exc:
            malformed += 1
            logger.warning(
                "skipping unkeyable url for %s: %s (%s)", company_slug, url, exc
            )
    return tuple(pairs), malformed


async def observe(
    session_factory: async_sessionmaker[AsyncSession],
    companies: Sequence[Company],
) -> ObservationSummary:
    summaries: list[CompanyObservation] = []
    snapshot_keys: set[tuple[str, str]] = set()
    for company in companies:
        async with session_factory() as session:
            urls = await discovery_jobs_repo.list_company_urls(
                session, company_slug=company.slug
            )
        observations, malformed = keyed_urls(company.slug, urls)
        async with session_factory() as session:
            summary = await jobs_repo.observe(
                session,
                company.slug,
                observations,
                utc_now(),
            )
        snapshot_keys.update(
            (company.slug, canonical_key) for canonical_key in summary.snapshot_keys
        )
        summaries.append(
            CompanyObservation(
                company_slug=company.slug,
                raw_urls=len(urls),
                snapshot_jobs=len(summary.snapshot_keys),
                new=summary.new,
                reseen=summary.reseen,
                malformed=malformed,
            )
        )
    return ObservationSummary(tuple(summaries), frozenset(snapshot_keys))


__all__ = ["CompanyObservation", "ObservationSummary", "keyed_urls", "observe"]
