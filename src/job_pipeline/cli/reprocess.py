"""Explicit auditable pipeline reprocessing."""

from __future__ import annotations

import argparse

from dotenv import load_dotenv

from job_discovery.catalog import COMPANIES, find_company
from job_pipeline.cli import run as run_command
from job_pipeline.cli.common import (
    add_database_argument,
    add_policy_arguments,
    fail,
    open_database,
    run_async,
)
from job_pipeline.persistence import jobs_repo

REPROCESS_SUMMARY = "Reprocess explicit jobs from an exact phase."
REPROCESS_DESCRIPTION = "Re-run selected jobs while preserving historical audit rows."


def add_arguments(parser: argparse.ArgumentParser) -> None:
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--job", action="append", type=int, metavar="ID")
    selection.add_argument("--status", choices=("unknown",))
    parser.add_argument("-c", "--company", metavar="HANDLE")
    parser.add_argument("--reason", required=True)
    parser.add_argument(
        "--from-phase",
        choices=("fetch", "eligibility", "extraction"),
        default="fetch",
    )
    add_database_argument(parser)
    add_policy_arguments(parser)
    parser.add_argument("--json-output", metavar="DIR", default=None)


async def _run(args: argparse.Namespace) -> None:
    reason = args.reason.strip()
    if not reason:
        fail("--reason must not be blank.")
    catalog = {company.slug: company for company in COMPANIES}
    async with open_database(args) as session_factory:
        if args.job:
            if args.company:
                fail("--company is only valid with --status unknown.")
            ids = tuple(dict.fromkeys(args.job))
            if any(job_id < 1 for job_id in ids):
                fail("Every --job ID must be 1 or greater.")
            async with session_factory() as session:
                jobs = await jobs_repo.list_by_ids(session, ids)
            found = {job.id for job in jobs}
            missing = [job_id for job_id in ids if job_id not in found]
            if missing:
                fail(f"Unknown job ID(s): {', '.join(map(str, missing))}")
        else:
            if not args.company:
                fail("--status unknown requires -c/--company HANDLE.")
            company = find_company(args.company)
            if company is None:
                fail(f"Unknown company handle(s): {args.company}")
            async with session_factory() as session:
                jobs = await jobs_repo.list_by_status(session, company.slug, "unknown")
            ids = tuple(job.id for job in jobs)
            if not ids:
                fail(f"No unknown jobs found for {company.slug}.")
        missing_companies = sorted({job.company_slug for job in jobs} - catalog.keys())
        if missing_companies:
            fail(f"Company missing from catalog: {', '.join(missing_companies)}")
        try:
            async with session_factory() as session:
                prepared = await jobs_repo.prepare_reprocess(
                    session, ids, from_phase=args.from_phase
                )
        except ValueError as exc:
            fail(str(exc))
    companies = list(
        {job.company_slug: catalog[job.company_slug] for job in jobs}.values()
    )
    await run_command.run_selected(args, companies, reprocess=prepared, reason=reason)


def execute(args: argparse.Namespace) -> None:
    load_dotenv()
    run_command.preflight_provider(args.model)
    run_async(_run(args))


__all__ = [
    "REPROCESS_DESCRIPTION",
    "REPROCESS_SUMMARY",
    "add_arguments",
    "execute",
]
