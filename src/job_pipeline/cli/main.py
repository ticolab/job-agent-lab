"""The ``job-pipeline`` subcommand dispatcher."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from job_pipeline.cli import eligibility, extraction, preview, reprocess, run, show


@dataclass(frozen=True, slots=True)
class Subcommand:
    name: str
    summary: str
    description: str
    add_arguments: Callable[[argparse.ArgumentParser], None]
    execute: Callable[[argparse.Namespace], None]
    passthrough: bool = False


SUBCOMMANDS: tuple[Subcommand, ...] = (
    Subcommand(
        "preview",
        preview.PREVIEW_SUMMARY,
        preview.PREVIEW_DESCRIPTION,
        preview.add_arguments,
        preview.execute,
    ),
    Subcommand(
        "run",
        run.RUN_SUMMARY,
        run.RUN_DESCRIPTION,
        run.add_arguments,
        run.execute,
        passthrough=True,
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
            add_help=not command.passthrough,
        )
        command.add_arguments(subparser)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args, extras = parser.parse_known_args(argv)
    if extras and not _BY_NAME[args.command].passthrough:
        parser.error(f"unrecognized arguments: {' '.join(extras)}")
    try:
        _BY_NAME[args.command].execute(args)
    except KeyboardInterrupt:
        # The run was cancelled and unwound; jobs left in progress are
        # released to retry by the next run's reaper.
        print(
            "Interrupted. In-progress jobs will be reaped by the next run.",
            file=sys.stderr,
        )
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
