"""The per-company unit of work.

:func:`run_company` is what the scheduler schedules. It owns one
company's whole lifecycle — skip check, run row, extraction, persistence
— and it is the boundary where a third-party board's failure stops being
an exception and becomes a recorded outcome.

Two properties are load-bearing.

**No single board aborts the batch.** Failure modes across 250
third-party sites are open-ended: Playwright crashes, provider rate
limits, DNS, a redesign that breaks a selector. Every one of them is
caught here and written to ``company_runs`` for an operator to read.
``KeyboardInterrupt``, ``SystemExit``, and ``asyncio.CancelledError``
derive from ``BaseException`` and still propagate, so Ctrl-C and task
cancellation behave normally.

**A session is never held across the extraction.** Sessions are taken
around each repository call and released before the minutes-long
``extract`` await. Holding one would pin a pool connection — and, under
SQLite, potentially a write lock — for the length of a browser session.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vacantes.batch.policy import BatchPolicy, should_skip
from vacantes.domain.company import Company
from vacantes.extraction.base import RunContext, get_strategy
from vacantes.persistence import jobs_repo, runs_repo
from vacantes.persistence.timestamps import utc_now

OutcomeStatus = Literal["success", "failed", "skipped"]

SessionFactory = async_sessionmaker[AsyncSession]


@dataclass(frozen=True)
class CompanyOutcome:
    """What happened to one company in one batch.

    The value every scheduled task resolves to. Because the worker
    catches its own failures, ``asyncio.gather`` needs no
    ``return_exceptions=True`` and aggregation needs no ``isinstance``
    checks — every element is one of these.

    A ``"skipped"`` outcome carries no run id because skipped companies
    write no row at all: ``company_runs`` records attempts, not
    considerations.

    Attributes:
        company: The company this outcome belongs to.
        status: ``"success"``, ``"failed"``, or ``"skipped"``.
        report: The extraction report on success, else ``None``. The
            same dict :func:`~vacantes.extraction.base.build_report`
            authors, so a caller can render it through
            :mod:`vacantes.reporting` unchanged.
        error: The recorded failure message on failure, else ``None``.
            Byte-identical to what was written to
            ``company_runs.error_message``.
    """

    company: Company
    status: OutcomeStatus
    report: dict[str, Any] | None = None
    error: str | None = None

    @classmethod
    def succeeded(cls, company: Company, report: dict[str, Any]) -> CompanyOutcome:
        """Build the outcome for a company that extracted successfully."""
        return cls(company=company, status="success", report=report)

    @classmethod
    def failed(cls, company: Company, error: str) -> CompanyOutcome:
        """Build the outcome for a company whose run raised."""
        return cls(company=company, status="failed", error=error)

    @classmethod
    def skipped(cls, company: Company) -> CompanyOutcome:
        """Build the outcome for a company that was already fresh."""
        return cls(company=company, status="skipped")


async def _fail(session_factory: SessionFactory, *, run_id: int, error: str) -> None:
    """Record *error* against *run_id* in its own short-lived session."""
    async with session_factory() as session:
        await runs_repo.fail_run(session, run_id=run_id, error=error)


async def run_company(
    company: Company,
    ctx: RunContext,
    *,
    session_factory: SessionFactory,
    policy: BatchPolicy,
    batch_id: str,
) -> CompanyOutcome:
    """Run one company end to end and record the outcome.

    The worker receives its session factory rather than importing a
    global engine, which is what lets the batch tests run against a
    temporary database — and what keeps this module from deciding where
    the database lives.

    An extraction that returns zero jobs is a **successful run with an
    unhappy verdict**, not a failure. Only an exception marks ``failed``,
    so retries target genuine breakage rather than boards that are
    honestly empty.

    Args:
        company: The company to extract.
        ctx: The same :class:`RunContext` the integration CLI passes, so
            a scheduled run and an onboarding run execute identical
            code. See ``TRANSITION.md`` §3.2.
        session_factory: Opens one short-lived session per repository
            call.
        policy: Supplies the freshness window and the per-company
            timeout.
        batch_id: Stamped on the ``company_runs`` row so one batch's
            attempts can be queried together.

    Returns:
        The :class:`CompanyOutcome`. Never raises for a board-level
        failure.
    """
    # One session per repository call, never one spanning several. Each
    # repository function opens its own transaction, and a session that
    # has already autobegun one for a read cannot begin another.
    async with session_factory() as session:
        if await should_skip(session, company=company, policy=policy):
            return CompanyOutcome.skipped(company)

    async with session_factory() as session:
        run_id = await runs_repo.start_run(
            session, company_slug=company.slug, batch_id=batch_id
        )

    try:
        report = await asyncio.wait_for(
            get_strategy(company.strategy).extract(company, ctx),
            timeout=policy.timeout.total_seconds(),
        )
    except TimeoutError:
        # Deliberately not `repr(exc)`: an asyncio timeout carries no
        # message, so the useful fact is the ceiling that was hit.
        message = f"timeout after {policy.timeout.total_seconds():g}s"
        await _fail(session_factory, run_id=run_id, error=message)
        return CompanyOutcome.failed(company, message)
    except Exception as exc:  # noqa: BLE001 — the isolation boundary
        message = repr(exc)
        await _fail(session_factory, run_id=run_id, error=message)
        return CompanyOutcome.failed(company, message)

    jobs: list[str] = report["jobs"]
    async with session_factory() as session:
        # `url_count` is the number of rows actually stored, which is the
        # de-duplicated count, not `len(jobs)`. A board that emits the
        # same posting twice would otherwise record a count that no
        # `select count(*) from job_urls` could ever reproduce. The
        # verdict beside it is computed from the unfiltered extraction,
        # so the two answer different questions on purpose.
        url_count = await jobs_repo.replace_company_urls(
            session,
            company_slug=company.slug,
            urls=jobs,
            captured_at=utc_now(),
        )

    # A second transaction rather than one spanning both, for the reason
    # above. Dying in between stores the URLs but leaves the run
    # `in_progress`, which the next batch reaps to `failed` and re-runs —
    # and re-running replaces the same set, so the window is harmless.
    async with session_factory() as session:
        await runs_repo.finish_run(
            session,
            run_id=run_id,
            url_count=url_count,
            verdict=report["metadata"]["verdict"],
        )

    return CompanyOutcome.succeeded(company, report)
