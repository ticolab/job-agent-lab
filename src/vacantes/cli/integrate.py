"""The ``integrate`` CLI surface: extract company-by-company.

Drives one company at a time (or the whole corpus, sequentially) and
writes a JSON report per company for a human to review — the evidence
the ``integrate-company`` workflow inspects before a catalog entry is
committed. Concurrency and persistence are deliberately absent here;
those belong to the ``batch`` surface.

This module owns argparse + the per-company orchestration loop. The heavy
lifting is delegated to sibling packages:

- ``extraction.base.get_strategy`` looks up the strategy the CLI dispatches
  through — ``DomStrategy`` for the default DOM path,
  ``GreenhouseStrategy`` for the Greenhouse-API path.
- ``reporting.output`` renders the result to console and JSON on disk.
- ``catalog`` supplies the ``COMPANIES`` list and ``find_company`` lookup.
- ``domain.region.COSTA_RICA_LATAM`` supplies the target region every run
  is scoped to (parameterising the region per-run is).
- ``settings`` supplies ``DEFAULT_MODEL``, ``DEFAULT_MAX_STEPS``,
  ``OUTPUT_DIR``.

Reachable through two entry points, both of which execute this module's
code with an identically-defined argument surface:

- the frozen ``job-agent-lab`` console script, which ``pyproject.toml``
  points straight at :func:`main` here rather than through the
  dispatcher, so its arguments are the pre-rename CLI's by construction;
- ``vacantes integrate``, whose subparser is populated by
  :func:`add_integrate_arguments` — the same function :func:`parse_args`
  uses for the alias's own parser, which is what makes drift between the
  two surfaces impossible rather than merely unlikely.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from vacantes.catalog import COMPANIES, find_company
from vacantes.catalog import _acronym as _catalog_acronym
from vacantes.domain.region import COSTA_RICA_LATAM
from vacantes.extraction.base import RunContext, get_strategy
from vacantes.reporting.output import print_summary, save_result
from vacantes.settings import DEFAULT_MAX_STEPS, DEFAULT_MODEL, OUTPUT_DIR


async def run_extraction(args: argparse.Namespace) -> None:
    """Run agent extraction for selected companies."""
    output_dir = Path(args.output_dir)

    if args.company:
        company = find_company(args.company)
        if not company:
            print(f"Company '{args.company}' not found. Available:")
            for c in COMPANIES:
                handles = [_catalog_acronym(c.name), *c.aliases]
                hint = ", ".join(dict.fromkeys(h for h in handles if h))
                print(f"  - {c.name}  (try: {hint})")
            sys.exit(1)
        companies = [company]
    else:
        companies = COMPANIES

    # One ``RunContext`` per invocation: the model / headless / step-cap
    # knobs are process-wide (parsed once from argv) and the region is
    # hard-wired to ``COSTA_RICA_LATAM`` until parameterises it.
    ctx = RunContext(
        model=args.model,
        headless=not args.headed,
        max_steps=args.max_steps,
        region=COSTA_RICA_LATAM,
    )

    print(f"\nvacantes - Extracting jobs from {len(companies)} company(ies)")
    print(
        f"Model: {args.model} | Max steps: {args.max_steps} | "
        f"Headless: {not args.headed}"
    )

    # accumulate every per-company report so the ``--strict``
    # exit gate can inspect the corpus-wide set of verdicts *after*
    # every company has been processed and saved. Draining the loop
    # first (rather than short-circuiting on the first non-match)
    # is deliberate — a strict run should still leave a full set of
    # JSON artefacts on disk for the operator to inspect.
    results: list[dict] = []
    for company in companies:
        print(f"\nProcessing: {company.name} (strategy={company.strategy})...")
        strategy = get_strategy(company.strategy)
        result = await strategy.extract(company, ctx)

        filepath = save_result(result, output_dir)
        print_summary(result)
        print(f"  Output:   {filepath}")
        results.append(result)

    if args.strict and _has_non_match(results):
        # Post-loop gate. The default path (``--strict`` off) never
        # reaches this branch, so corpus behaviour is byte-identical
        # to the pre-verdict CLI.
        sys.exit(1)


def _has_non_match(results: list[dict]) -> bool:
    """Return ``True`` if any accumulated report is not a ``match``.

    Treats ``"unverified"``, ``"under"``, and ``"over"`` uniformly as
    non-match: a never-counted company (``expected_jobs=None``) is
    not a passing run under ``--strict``, which is the intended
    forcing function for backfilling the corpus's human counts.
    Empty ``results`` returns ``False`` (nothing ran → nothing to
    fail on); the CLI still exits 0 in that edge case, matching
    the pre-verdict no-op semantics.
    """
    return any(r["metadata"]["verdict"] != "match" for r in results)


INTEGRATE_DESCRIPTION = "Extract job postings from one company, or the whole corpus."


def add_integrate_arguments(parser: argparse.ArgumentParser) -> None:
    """Populate *parser* with the integrate surface's arguments.

    Factored out of :func:`parse_args` so the ``vacantes integrate``
    subparser and the frozen ``job-agent-lab`` alias's own parser are
    populated by one function call rather than two hand-maintained
    lists. The ``integrate-company`` workflow depends on this surface
    being stable, and a shared definition makes divergence a
    structural impossibility instead of a review obligation.
    """
    parser.add_argument(
        "-c",
        "--company",
        type=str,
        default=None,
        help="Company name to extract (partial match). Defaults to all.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        default=False,
        help="Run browser in headed mode for debugging.",
    )
    parser.add_argument(
        "-m",
        "--model",
        type=str,
        default=DEFAULT_MODEL,
        help=f"OpenAI model to use (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=DEFAULT_MAX_STEPS,
        help=f"Max agent steps (default: {DEFAULT_MAX_STEPS}).",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=str,
        default=str(OUTPUT_DIR),
        help=f"Output directory for JSON results (default: {OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help=(
            "Exit non-zero if any run's metadata.verdict is not 'match'. "
            "The four verdicts are 'match' (found == expected), 'under' "
            "(found < expected), 'over' (found > expected), and "
            "'unverified' (expected_jobs=None). Under --strict, "
            "'unverified' counts as non-match — a never-counted company "
            "fails the gate, which is the intended forcing function for "
            "backfilling expected_jobs across the corpus. Every company "
            "still runs to completion and every result is saved to disk; "
            "the exit code is decided after the loop drains."
        ),
    )


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the standalone ``job-agent-lab`` alias."""
    parser = argparse.ArgumentParser(description=INTEGRATE_DESCRIPTION)
    add_integrate_arguments(parser)
    return parser.parse_args()


def execute(args: argparse.Namespace) -> None:
    """Run the integrate workflow for an already-parsed *args*.

    Holds the process-level setup — ``.env`` loading and root logging
    configuration — so the alias and the ``vacantes integrate``
    subcommand perform identical setup rather than each reimplementing
    it. The dispatcher in :mod:`vacantes.cli.main` calls this directly
    with the namespace its own subparser produced.
    """
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(run_extraction(args))


def main() -> None:
    """Entry point for the frozen ``job-agent-lab`` console script."""
    execute(parse_args())


if __name__ == "__main__":
    main()
