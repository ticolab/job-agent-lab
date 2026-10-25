"""Preview pipeline dispositions without constructing variable-cost dependencies."""

from __future__ import annotations

import argparse
from collections.abc import Collection, Sequence

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from job_discovery.domain.company import Company
from job_discovery.persistence import jobs_repo as discovery_jobs_repo
from job_pipeline.cli.common import (
    add_database_argument,
    add_policy_arguments,
    add_selection_arguments,
    open_database,
    run_async,
    select_companies,
)
from job_pipeline.persistence import jobs_repo
from job_pipeline.runner.observe import keyed_urls, observe

PREVIEW_SUMMARY = "Preview pipeline work without processing it."
PREVIEW_DESCRIPTION = "Observe selected snapshots and print current dispositions."
_DISPOSITIONS = (
    ("new", "new"),
    ("retry_due", "retry due"),
    ("retry_waiting", "retry waiting"),
    ("in_progress", "in progress"),
    ("completed_eligible", "completed eligible"),
    ("completed_ineligible", "completed ineligible"),
    ("unknown", "unknown"),
    ("unavailable", "unavailable"),
    ("failed", "failed"),
    ("merged", "merged"),
)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    add_selection_arguments(parser)
    add_database_argument(parser)
    add_policy_arguments(parser)
    parser.add_argument("--no-observe", action="store_true", default=False)
    parser.add_argument("--by-company", action="store_true", default=False)


async def _snapshot_keys(
    session_factory: async_sessionmaker[AsyncSession], companies: Sequence[Company]
) -> frozenset[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for company in companies:
        async with session_factory() as session:
            urls = await discovery_jobs_repo.list_company_urls(
                session, company_slug=company.slug
            )
        pairs, _ = keyed_urls(company.slug, urls)
        keys.update((company.slug, key) for key, _ in pairs)
    return frozenset(keys)


def _would_process(counts: Collection[tuple[str, int]], limit: int | None) -> int:
    count_map = dict(counts)
    total = count_map.get("new", 0) + count_map.get("retry_due", 0)
    return min(total, limit) if limit is not None else total


def _print_counts(counts: dict[str, int], limit: int | None, indent: str = "") -> None:
    for key, label in _DISPOSITIONS:
        print(f"{indent}{label:<22} {counts.get(key, 0)}")
    print(f"{indent}would process          {_would_process(counts.items(), limit)}")


async def preview(args: argparse.Namespace, companies: Sequence[Company]) -> None:
    async with open_database(args) as session_factory:
        if args.no_observe:
            snapshot_keys = await _snapshot_keys(session_factory, companies)
        else:
            observation = await observe(session_factory, companies)
            snapshot_keys = observation.snapshot_keys
        async with session_factory() as session:
            counts = await jobs_repo.disposition_counts(session, snapshot_keys)
        print("Pipeline preview")
        _print_counts(counts, args.limit)
        if args.by_company:
            for company in companies:
                company_keys = {key for key in snapshot_keys if key[0] == company.slug}
                async with session_factory() as session:
                    company_counts = await jobs_repo.disposition_counts(
                        session, company_keys
                    )
                print(f"\n{company.slug}")
                _print_counts(company_counts, args.limit, "  ")


def execute(args: argparse.Namespace) -> None:
    companies = select_companies(args)
    run_async(preview(args, companies))


__all__ = [
    "PREVIEW_DESCRIPTION",
    "PREVIEW_SUMMARY",
    "add_arguments",
    "execute",
    "preview",
]
