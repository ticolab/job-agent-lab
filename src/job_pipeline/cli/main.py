"""The ``job-pipeline`` subcommand dispatcher."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from job_pipeline.cli import (
    eligibility,
    extraction,
    integrate,
    preview,
    reprocess,
    show,
)


@dataclass(frozen=True, slots=True)
class Subcommand:
    name: str
    summary: str
    description: str
    add_arguments: Callable[[argparse.ArgumentParser], None]
    execute: Callable[[argparse.Namespace], None]


SUBCOMMANDS: tuple[Subcommand, ...] = (
    Subcommand(
        "integrate",
        integrate.INTEGRATE_SUMMARY,
        integrate.INTEGRATE_DESCRIPTION,
        integrate.add_arguments,
        integrate.execute,
    ),
    Subcommand(
        "preview",
        preview.PREVIEW_SUMMARY,
        preview.PREVIEW_DESCRIPTION,
        preview.add_arguments,
        preview.execute,
    ),
    Subcommand(
        "eligibility",
        eligibility.ELIGIBILITY_SUMMARY,
        eligibility.ELIGIBILITY_DESCRIPTION,
        eligibility.add_arguments,
        eligibility.execute,
    ),
    Subcommand(
        "extraction",
        extraction.EXTRACTION_SUMMARY,
        extraction.EXTRACTION_DESCRIPTION,
        extraction.add_arguments,
        extraction.execute,
    ),
    Subcommand(
        "reprocess",
        reprocess.REPROCESS_SUMMARY,
        reprocess.REPROCESS_DESCRIPTION,
        reprocess.add_arguments,
        reprocess.execute,
    ),
    Subcommand(
        "show",
        show.SHOW_SUMMARY,
        show.SHOW_DESCRIPTION,
        show.add_arguments,
        show.execute,
    ),
)
_BY_NAME = {command.name: command for command in SUBCOMMANDS}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="job-pipeline",
        description="Evaluate and structure discovered job postings.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in SUBCOMMANDS:
        subparser = subparsers.add_parser(
            command.name,
            help=command.summary,
            description=command.description,
        )
        command.add_arguments(subparser)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        _BY_NAME[args.command].execute(args)
    except KeyboardInterrupt:
        message = (
            "Interrupted."
            if args.command == "integrate"
            else "Interrupted. In-progress jobs will be reaped by the next run."
        )
        print(message, file=sys.stderr)
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
