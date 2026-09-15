"""The ``vacantes batch`` subcommand — not yet implemented.

Placeholder registered with the dispatcher so the subcommand table in
:mod:`vacantes.cli.main` has one shape from the start and ``vacantes
--help`` advertises the eventual surface. The scheduler it will drive
(``vacantes/batch/``) lands in a later phase, together with the
persistence layer it writes through; the flag surface is defined at the
same time so it is specified once against working code rather than
twice.

Exiting non-zero with an explicit message is deliberate. A subcommand
that silently no-ops would be indistinguishable from a batch that ran
and found nothing — the single most expensive confusion available in a
tool whose entire job is reporting what is open.
"""

from __future__ import annotations

import argparse
import sys

BATCH_DESCRIPTION = (
    "Run extraction across the corpus concurrently and persist the "
    "current job-URL set (not yet implemented)."
)

BATCH_SUMMARY = "Run the corpus concurrently and persist results."

_NOT_IMPLEMENTED = (
    "vacantes batch is not implemented yet. Use 'job-agent-lab -c "
    "<handle>' or 'vacantes integrate -c <handle>' to extract a single "
    "company."
)


def add_batch_arguments(parser: argparse.ArgumentParser) -> None:
    """Populate *parser* with the batch surface's arguments.

    Intentionally empty while the subcommand is a placeholder: adding
    flags that are parsed and then discarded would let a caller write a
    command line that looks accepted but does nothing.
    """


def execute(args: argparse.Namespace) -> None:
    """Refuse to run, pointing the caller at the working entry point."""
    del args  # No arguments are defined yet; nothing to consume.
    print(_NOT_IMPLEMENTED, file=sys.stderr)
    raise SystemExit(2)
