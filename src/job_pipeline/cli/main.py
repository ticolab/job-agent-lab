"""The ``job-pipeline`` subcommand dispatcher."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from job_pipeline.cli import preview, reprocess, run, show


@dataclass(frozen=True, slots=True)
class Subcommand:
    name: str
    summary: str
    description: str
    add_arguments: Callable[[argparse.ArgumentParser], None]
    execute: Callable[[argparse.Namespace], None]


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
    args = build_parser().parse_args(argv)
    _BY_NAME[args.command].execute(args)


if __name__ == "__main__":
    main()
