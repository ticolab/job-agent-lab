"""Fold discovery snapshots into durable company-scoped pipeline jobs."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from job_discovery.domain.company import Company
from job_discovery.persistence import jobs_repo as discovery_jobs_repo
from job_discovery.persistence.timestamps import utc_now
from job_pipeline.identity import url_key
from job_pipeline.persistence import jobs_repo


@dataclass(frozen=True, slots=True)
class CompanyObservation:
    company_slug: str
    raw_urls: int
    snapshot_jobs: int
    new: int
    reseen: int

    @property
    def collapsed(self) -> int:
        return self.raw_urls - self.snapshot_jobs


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
        observations = tuple((url_key(url), url) for url in urls)
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
            )
        )
    return ObservationSummary(tuple(summaries), frozenset(snapshot_keys))


__all__ = ["CompanyObservation", "ObservationSummary", "observe"]
