from __future__ import annotations

import argparse

from job_pipeline.cli.common import fail

RUN_SUMMARY = "Retired: use eligibility and extraction."
RUN_DESCRIPTION = RUN_SUMMARY


def add_arguments(parser: argparse.ArgumentParser) -> None:
    pass


def execute(args: argparse.Namespace) -> None:
    fail("Retired: use job-pipeline eligibility and job-pipeline extraction.")
