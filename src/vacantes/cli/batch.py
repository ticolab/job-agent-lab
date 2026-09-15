"""The ``vacantes batch`` subcommand: run the corpus and persist results.

The operational entry point. Where ``integrate`` drives one board at a
time and leaves a JSON artifact for a human to review, this drives the
corpus concurrently and writes the current job-URL set to the database,
which is the artifact that answers *what is open right now*.

This module is the **composition root**. It is the layer that decides
where the database lives, opens the engine, and hands the session
factory down to the scheduler — which is exactly why
:mod:`vacantes.batch` needs no route to :mod:`vacantes.settings` or to a
global engine, and why its whole surface stays testable against a
temporary file. Wiring concrete dependencies is the outermost layer's
job, so ``cli`` reaching both ``batch`` and ``persistence`` is the
intended direction rather than a leak.

Three decisions worth stating, because none is forced by
``TRANSITION.md`` §7:

**A selection is required.** There is no bare ``vacantes batch`` that
means "everything". The corpus is approaching 250 boards, many of which
drive a real browser and a paid LLM, and a full run is measured in
hours — so the expensive mistake is the one an accidental keystroke
makes. ``--all`` says it out loud.

**A completed batch exits zero even when boards failed.** Individual
failures are *data*: they are recorded in ``company_runs`` with their
error text, and the whole point of the worker's isolation boundary is
that one broken site is an ordinary event rather than a failure of the
batch. This matches ``integrate``, which likewise exits zero on an
unhappy verdict unless ``--strict`` is passed. A non-zero exit means the
batch could not run at all: an unknown handle, an empty selection, or a
database with no schema.

**The batch summary is rendered here rather than in**
:mod:`vacantes.reporting`. Per-company JSON still routes through
``reporting.output.save_result``, so there remains exactly one JSON
renderer. But an aggregate summary reads a
:class:`~vacantes.batch.scheduler.BatchResult`, and teaching the
reporting layer about the batch package would invert a dependency to
buy symmetry that nothing needs.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import NoReturn

from dotenv import load_dotenv

from vacantes.batch.policy import (
    DEFAULT_BROWSER_CONCURRENCY,
    DEFAULT_FRESHNESS,
    DEFAULT_HTTP_CONCURRENCY,
    DEFAULT_TIMEOUT,
    BatchPolicy,
    concurrency_class,
    should_skip,
)
from vacantes.batch.scheduler import BatchResult, run_batch
from vacantes.batch.worker import SessionFactory
from vacantes.catalog import COMPANIES, find_company
from vacantes.domain.company import Company
from vacantes.domain.region import COSTA_RICA_LATAM
from vacantes.extraction.base import RunContext
from vacantes.persistence.engine import database, missing_tables
from vacantes.reporting.output import save_result
from vacantes.settings import (
    DATABASE_ENV_VAR,
    DATABASE_PATH,
    DEFAULT_MAX_STEPS,
    DEFAULT_MODEL,
)

BATCH_SUMMARY = "Run the corpus concurrently and persist results."

BATCH_DESCRIPTION = (
    "Run extraction across the corpus concurrently and persist the current "
    "job-URL set. Companies that already succeeded inside the freshness "
    "window are skipped, so re-running after a partial failure retries only "
    "what is owed. Per-company failures are recorded in the database rather "
    "than signalled by the exit code."
)

_RULE = "=" * 60


def _fail(message: str) -> NoReturn:
    """Abort with *message* before any work starts."""
    print(message, file=sys.stderr)
    raise SystemExit(2)


def _positive_int(text: str) -> int:
    """An ``int`` of at least 1.

    A concurrency ceiling of zero would build a semaphore nobody can
    ever acquire, so the batch would hang rather than refuse — which is
    the worst of the available failure modes.
    """
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be 1 or greater, got {value}")
    return value


def _non_negative_float(text: str) -> float:
    """A ``float`` of at least 0, for the freshness window.

    Zero is meaningful and allowed: it makes every company eligible,
    which is ``--force`` expressed as a window.
    """
    value = float(text)
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be 0 or greater, got {value}")
    return value


def add_batch_arguments(parser: argparse.ArgumentParser) -> None:
    """Populate *parser* with the batch surface's arguments."""
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--all",
        action="store_true",
        default=False,
        help="Run every company in the catalog.",
    )
    selection.add_argument(
        "-c",
        "--company",
        action="append",
        metavar="HANDLE",
        default=None,
        help=(
            "Run one company, resolved exactly as 'integrate -c' resolves "
            "it (alias, then acronym, then name substring). Repeat the flag "
            "to name several: -c speechify -c cloudbeds."
        ),
    )
    selection.add_argument(
        "--companies",
        metavar="FILE",
        default=None,
        help=(
            "Run the companies listed in FILE, one handle per line. Blank "
            "lines and '#' comments are ignored."
        ),
    )
    parser.add_argument(
        "--exclude",
        action="append",
        metavar="HANDLE",
        default=None,
        help="Drop a company from the selection. Repeatable.",
    )
    parser.add_argument(
        "--browser-concurrency",
        type=_positive_int,
        default=DEFAULT_BROWSER_CONCURRENCY,
        metavar="N",
        help=(
            "Simultaneous browser-class runs, each holding a Chromium "
            f"context (default: {DEFAULT_BROWSER_CONCURRENCY})."
        ),
    )
    parser.add_argument(
        "--http-concurrency",
        type=_positive_int,
        default=DEFAULT_HTTP_CONCURRENCY,
        metavar="N",
        help=(
            "Simultaneous HTTP-class runs, each a plain API call "
            f"(default: {DEFAULT_HTTP_CONCURRENCY})."
        ),
    )
    parser.add_argument(
        "--freshness-hours",
        type=_non_negative_float,
        default=DEFAULT_FRESHNESS.total_seconds() / 3600,
        metavar="N",
        help=(
            "Skip a company that already succeeded within this many hours "
            f"(default: {DEFAULT_FRESHNESS.total_seconds() / 3600:g}). "
            "Failures are never skipped, so a broken board stays eligible."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Ignore freshness and run every selected company.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=_positive_int,
        default=int(DEFAULT_TIMEOUT.total_seconds()),
        metavar="N",
        help=(
            "Per-company ceiling; exceeding it records the run as failed "
            f"(default: {int(DEFAULT_TIMEOUT.total_seconds())}). Doubles as "
            "the staleness bound for reaping runs a killed batch abandoned, "
            "since no run may legitimately outlive its own ceiling — so "
            "lowering this to reap sooner also shortens every run."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help=(
            "Print the plan and exit without writing anything: which "
            "companies would run, which are skipped as fresh, and the "
            "concurrency ceilings. The first thing to reach for when a "
            "batch misbehaves."
        ),
    )
    parser.add_argument(
        "--json-output",
        metavar="DIR",
        default=None,
        help=(
            "Additionally write one JSON report per successful company, in "
            "the same format 'integrate' writes."
        ),
    )
    parser.add_argument(
        "--database",
        metavar="PATH",
        default=None,
        help=(
            f"SQLite database to write (default: {DATABASE_PATH}, or the "
            f"{DATABASE_ENV_VAR} environment variable). Alembic has no such "
            f"flag and reads only {DATABASE_ENV_VAR}, so a database named "
            "here must be migrated with that variable set."
        ),
    )
    parser.add_argument(
        "-m",
        "--model",
        type=str,
        default=DEFAULT_MODEL,
        help=(
            "OpenAI model for browser-class companies that drive an agent "
            f"(default: {DEFAULT_MODEL})."
        ),
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=DEFAULT_MAX_STEPS,
        help=f"Max agent steps per company (default: {DEFAULT_MAX_STEPS}).",
    )
    # No `--headed`: the flag exists on `integrate` to debug one board,
    # and several concurrent headed browsers fighting for the screen is
    # not a thing anyone wants. Debug a board through `integrate`.


def _read_handle_file(path: Path) -> list[str]:
    """Read one handle per line from *path*, ignoring blanks and comments."""
    try:
        text = path.read_text()
    except OSError as exc:
        _fail(f"Cannot read --companies file '{path}': {exc}")
    handles = []
    for raw_line in text.splitlines():
        handle = raw_line.split("#", 1)[0].strip()
        if handle:
            handles.append(handle)
    return handles


def _resolve(handles: Sequence[str]) -> tuple[list[Company], list[str]]:
    """Resolve *handles* through ``find_company``.

    Returns the companies found and the handles that matched nothing,
    rather than failing on the first miss: an operator fixing a list of
    twenty handles should learn about every typo in one run.
    """
    found: list[Company] = []
    unknown: list[str] = []
    for handle in handles:
        company = find_company(handle)
        if company is None:
            unknown.append(handle)
        else:
            found.append(company)
    return found, unknown


def select_companies(args: argparse.Namespace) -> list[Company]:
    """Resolve the selection flags to a de-duplicated company list.

    Every failure mode here aborts before the batch touches anything,
    which is the point: discovering a misspelled handle an hour into a
    250-board run is not a recoverable situation.
    """
    if args.all:
        selected = list(COMPANIES)
    else:
        handles = (
            _read_handle_file(Path(args.companies))
            if args.companies
            else list(args.company or ())
        )
        if not handles:
            _fail("No company handles given. Pass --all, -c HANDLE, or --companies.")
        selected, unknown = _resolve(handles)
        if unknown:
            _fail(f"Unknown company handle(s): {', '.join(unknown)}")

    if args.exclude:
        excluded, unknown = _resolve(args.exclude)
        if unknown:
            _fail(f"Unknown --exclude handle(s): {', '.join(unknown)}")
        dropped = {company.slug for company in excluded}
        selected = [c for c in selected if c.slug not in dropped]

    # Two handles can resolve to one company; dict keyed on slug
    # de-duplicates while preserving the order they were given in.
    ordered = list({company.slug: company for company in selected}.values())
    if not ordered:
        _fail("No companies selected after exclusions.")
    return ordered


def build_policy(args: argparse.Namespace) -> BatchPolicy:
    """Build the :class:`BatchPolicy` the flags describe."""
    return BatchPolicy(
        browser_concurrency=args.browser_concurrency,
        http_concurrency=args.http_concurrency,
        freshness=timedelta(hours=args.freshness_hours),
        timeout=timedelta(seconds=args.timeout_seconds),
        force=args.force,
    )


def _build_context(args: argparse.Namespace) -> RunContext:
    """Build the run context handed to every strategy.

    Headless is not a flag here and is always ``True``; see
    :func:`add_batch_arguments`.
    """
    return RunContext(
        model=args.model,
        headless=True,
        max_steps=args.max_steps,
        region=COSTA_RICA_LATAM,
    )


def _company_line(company: Company) -> str:
    """One aligned plan row: class, slug, strategy."""
    return f"    {concurrency_class(company):<8} {company.slug:<34} {company.strategy}"


def _print_plan(
    to_run: Sequence[Company],
    fresh: Sequence[Company],
    policy: BatchPolicy,
) -> None:
    """Render the dry-run plan."""
    hours = policy.freshness.total_seconds() / 3600
    print(f"\n{_RULE}")
    print("  Batch plan — dry run, nothing was written")
    print(f"{_RULE}")
    print(
        f"  Concurrency:  {policy.browser_concurrency} browser, "
        f"{policy.http_concurrency} http"
    )
    print(
        f"  Freshness:    {hours:g}h" + ("  (ignored: --force)" if policy.force else "")
    )
    print(f"  Timeout:      {policy.timeout.total_seconds():g}s per company")

    print(f"\n  Would run ({len(to_run)}):")
    if to_run:
        for company in to_run:
            print(_company_line(company))
    else:
        print("    (none)")

    if fresh:
        print(f"\n  Skipped as fresh ({len(fresh)}):")
        for company in fresh:
            print(_company_line(company))
    print(f"{_RULE}")


def _print_summary(result: BatchResult, policy: BatchPolicy) -> None:
    """Render the post-run summary."""
    hours = policy.freshness.total_seconds() / 3600
    stored = sum(o.url_count or 0 for o in result.outcomes if o.status == "success")

    print(f"\n{_RULE}")
    print(f"  Batch:     {result.batch_id}")
    print(f"  Companies: {len(result.outcomes)} selected")
    print(f"  Success:   {result.succeeded}")
    print(f"  Failed:    {result.failed}")
    print(f"  Skipped:   {result.skipped}  (succeeded within {hours:g}h)")
    if result.reaped:
        print(f"  Reaped:    {result.reaped}  (abandoned by an earlier batch)")
    print(f"  Job URLs:  {stored} stored across {result.succeeded} company(ies)")

    failures = [o for o in result.outcomes if o.status == "failed"]
    if failures:
        print(f"\n  Failures ({len(failures)}):")
        for outcome in failures:
            print(f"    {outcome.company.slug:<34} {outcome.error}")
    print(f"{_RULE}")


def _save_reports(result: BatchResult, output_dir: Path) -> None:
    """Write one JSON report per successful company.

    Routes through the same ``save_result`` the integrate surface uses,
    so a batch-produced artifact is byte-comparable with an
    onboarding-produced one.
    """
    for outcome in result.outcomes:
        if outcome.report is not None:
            save_result(outcome.report, output_dir)
    print(f"  JSON:      {result.succeeded} report(s) written to {output_dir}")


async def _plan(
    session_factory: SessionFactory,
    companies: Sequence[Company],
    policy: BatchPolicy,
) -> None:
    """Compute and print the dry-run plan.

    Reads freshness but writes nothing — no run rows, no catalog
    projection. One session serves every check because
    ``has_fresh_success`` is a plain read that opens no transaction of
    its own.
    """
    to_run: list[Company] = []
    fresh: list[Company] = []
    async with session_factory() as session:
        for company in companies:
            target = (
                fresh
                if await should_skip(session, company=company, policy=policy)
                else to_run
            )
            target.append(company)
    _print_plan(to_run, fresh, policy)


def _migrate_hint(path: Path | None) -> str:
    """The command that migrates the database this run would open.

    Alembic reads ``settings.DATABASE_PATH`` and has no flag of its own,
    so when ``--database`` names a different file the bare command would
    migrate the wrong one and the operator would be sent in a circle.
    The hint then carries the environment variable Alembic *does* read.
    """
    if path is None or path == DATABASE_PATH:
        return "uv run alembic upgrade head"
    return f"{DATABASE_ENV_VAR}={path} uv run alembic upgrade head"


async def _run(args: argparse.Namespace) -> None:
    """Open the database, then either plan or run the batch."""
    companies = select_companies(args)
    policy = build_policy(args)
    path = Path(args.database) if args.database else None

    async with database(path) as session_factory:
        async with session_factory() as session:
            absent = await missing_tables(session)
        if absent:
            _fail(
                f"Database schema is missing table(s): {', '.join(absent)}. "
                f"Run '{_migrate_hint(path)}' first."
            )

        if args.dry_run:
            await _plan(session_factory, companies, policy)
            return

        result = await run_batch(
            companies,
            _build_context(args),
            session_factory=session_factory,
            policy=policy,
        )

    _print_summary(result, policy)
    if args.json_output:
        _save_reports(result, Path(args.json_output))


def execute(args: argparse.Namespace) -> None:
    """Run the batch workflow for an already-parsed *args*.

    Performs the same process-level setup as the integrate surface —
    ``.env`` loading for the API key a browser-class company needs, and
    root logging configuration — so the two subcommands behave
    identically before their first line of real work.
    """
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(_run(args))
