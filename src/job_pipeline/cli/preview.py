from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence

from job_discovery.domain.company import Company
from job_discovery.persistence import catalog_repo
from job_discovery.persistence.timestamps import utc_now
from job_pipeline.cli.common import (
    add_database_argument,
    add_policy_arguments,
    add_selection_arguments,
    open_database,
    run_async,
    select_companies,
)
from job_pipeline.persistence import jobs_repo
from job_pipeline.persistence.models import STAGE_PHASES, Stage
from job_pipeline.runner.observe import observe, read_snapshot_keys

PREVIEW_SUMMARY = "Preview pipeline work without processing it."
PREVIEW_DESCRIPTION = "Observe selected snapshots and print stage dispositions."
_SECTIONS = (
    (
        "Eligibility work",
        (
            ("new", "new"),
            ("eligibility_retry_due", "retry due"),
            ("eligibility_retry_waiting", "retry waiting"),
        ),
    ),
    (
        "Extraction backlog",
        (
            ("ready_for_extraction", "ready for extraction"),
            ("extraction_retry_due", "retry due"),
            ("extraction_retry_waiting", "retry waiting"),
        ),
    ),
    ("In progress", (("in_progress", "in progress"),)),
    (
        "Terminal dispositions",
        tuple(
            (status, status.replace("_", " "))
            for status in (
                "completed_eligible",
                "completed_ineligible",
                "unknown",
                "unavailable",
                "failed",
                "merged",
            )
        ),
    ),
)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    add_selection_arguments(parser)
    add_database_argument(parser)
    add_policy_arguments(parser)
    parser.add_argument("--no-observe", action="store_true", default=False)
    parser.add_argument("--by-company", action="store_true", default=False)


def _print_counts(
    counts: dict[str, int],
    work_counts: Mapping[Stage, int],
    limit: int | None,
    indent: str = "",
) -> None:
    for title, rows in _SECTIONS:
        print(f"{indent}{title}")
        for key, label in rows:
            print(f"{indent}{label:<22} {counts.get(key, 0)}")
    for stage, total in work_counts.items():
        total = min(total, limit) if limit is not None else total
        print(f"{indent}would process ({stage}) {total}")


async def preview(args: argparse.Namespace, companies: Sequence[Company]) -> None:
    now = utc_now()
    async with open_database(args) as session_factory:
        if args.no_observe:
            snapshot_keys = await read_snapshot_keys(session_factory, companies)
        else:
            async with session_factory() as session:
                await catalog_repo.sync_catalog(session, companies)
            observation = await observe(session_factory, companies)
            snapshot_keys = observation.snapshot_keys
        async with session_factory() as session:
            counts = await jobs_repo.stage_dispositions(session, snapshot_keys, now)
            work_counts = {
                stage: await jobs_repo.count_work(
                    session, snapshot_keys, now, stage=stage
                )
                for stage in STAGE_PHASES
            }
        print("Pipeline preview")
        _print_counts(counts, work_counts, args.limit)
        if args.by_company:
            for company in companies:
                company_keys = {key for key in snapshot_keys if key[0] == company.slug}
                async with session_factory() as session:
                    company_counts = await jobs_repo.stage_dispositions(
                        session, company_keys, now
                    )
                    company_work_counts = {
                        stage: await jobs_repo.count_work(
                            session, company_keys, now, stage=stage
                        )
                        for stage in STAGE_PHASES
                    }
                print(f"\n{company.slug}")
                _print_counts(company_counts, company_work_counts, args.limit, "  ")


def execute(args: argparse.Namespace) -> None:
    companies = select_companies(args)
    run_async(preview(args, companies))
