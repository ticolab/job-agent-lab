"""The ``job-discovery`` dispatcher.

Owns exactly one decision: which subcommand the operator asked for.
Every subcommand contributes a :class:`Subcommand` row describing its
own parser and its own entry function, so this module never learns what
``integrate`` or ``scan`` actually do and adding a third costs one
tuple entry instead of a new branch.

Notably, the ``integrate`` row reuses
:func:`job_discovery.cli.integrate.add_integrate_arguments` — the very
function the frozen ``job-agent-lab`` alias uses to build its own
parser. There is one definition of that argument surface in the
codebase, which is what makes the two entry points provably identical
rather than identical by inspection.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass

from job_discovery.cli import integrate, scan


@dataclass(frozen=True)
class Subcommand:
    """One row of the dispatch table.

    Attributes:
        name: The token the operator types after ``job-discovery``.
        summary: One-line text shown in ``job-discovery --help``.
        description: Longer text shown in ``job-discovery <name> --help``.
        add_arguments: Populates the subcommand's parser.
        execute: Runs the subcommand against a parsed namespace.
    """

    name: str
    summary: str
    description: str
    add_arguments: Callable[[argparse.ArgumentParser], None]
    execute: Callable[[argparse.Namespace], None]


SUBCOMMANDS: tuple[Subcommand, ...] = (
    Subcommand(
        name="integrate",
        summary="Extract one company (or all) and write JSON reports.",
        description=integrate.INTEGRATE_DESCRIPTION,
        add_arguments=integrate.add_integrate_arguments,
        execute=integrate.execute,
    ),
    Subcommand(
        name="scan",
        summary=scan.SCAN_SUMMARY,
        description=scan.SCAN_DESCRIPTION,
        add_arguments=scan.add_scan_arguments,
        execute=scan.execute,
    ),
)

_BY_NAME: dict[str, Subcommand] = {sub.name: sub for sub in SUBCOMMANDS}


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level parser with one subparser per subcommand."""
    parser = argparse.ArgumentParser(
        prog="job-discovery",
        description="Job-posting extraction across a corpus of career sites.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for sub in SUBCOMMANDS:
        subparser = subparsers.add_parser(
            sub.name,
            help=sub.summary,
            description=sub.description,
        )
        sub.add_arguments(subparser)
    return parser


def main() -> None:
    """Entry point for the ``job-discovery`` console script."""
    args = build_parser().parse_args()
    _BY_NAME[args.command].execute(args)


if __name__ == "__main__":
    main()
