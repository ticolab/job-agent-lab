from __future__ import annotations

import argparse

from dotenv import load_dotenv

from job_pipeline.cli import preview, stages
from job_pipeline.cli.common import (
    add_database_argument,
    add_fetch_policy_arguments,
    add_model_policy_arguments,
    add_selection_arguments,
    run_async,
    select_companies,
)

ELIGIBILITY_SUMMARY = "Evaluate eligibility and stop at the extraction boundary."
ELIGIBILITY_DESCRIPTION = ELIGIBILITY_SUMMARY


def add_arguments(parser: argparse.ArgumentParser) -> None:
    add_selection_arguments(parser)
    add_database_argument(parser)
    add_model_policy_arguments(parser)
    add_fetch_policy_arguments(parser)
    parser.add_argument("--dry-run", action="store_true")


def execute(args: argparse.Namespace) -> None:
    companies = select_companies(args)
    if args.dry_run:
        args.no_observe = False
        args.by_company = False
        run_async(preview.preview(args, companies))
        return
    load_dotenv()
    stages.preflight_provider(args.model)
    run_async(stages.run_stage(args, companies, stage="eligibility"))
