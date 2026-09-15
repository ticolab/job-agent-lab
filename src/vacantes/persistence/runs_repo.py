"""Run lifecycle, freshness, and stale-run reaping.

A row enters ``company_runs`` as ``in_progress`` when a worker starts
and is updated to ``success`` or ``failed`` exactly once. Companies
skipped as fresh produce no row: this table records attempts, not
considerations.

Two rules in this module are the ones a reader should not have to
reconstruct.

**Freshness keys on successes only.** :func:`has_fresh_success` filters
``status = 'success'``. An ``updated_at``-style check that failures also
touched would treat a board that failed this morning as "done" this
afternoon, and it would then stop being retried entirely — the failure
would become permanent and silent. Keying on successes means failures
are always eligible for retry and successes are never repeated inside
the window.

**Crash recovery falls out of that rule.** A batch killed mid-run leaves
``in_progress`` rows with no ``finished_at``. :func:`reap_stale_runs`
marks them failed at the next batch's startup; because they are not
successes, the reaped companies fail the freshness check and re-run,
while companies that genuinely completed before the crash are skipped.
No checkpoint file is needed, and resumption is not a separate mechanism.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from vacantes.persistence.models import CompanyRun
from vacantes.persistence.timestamps import as_naive_utc, utc_now

# Written into ``error_message`` by :func:`reap_stale_runs`. A distinct,
# greppable string so an operator can tell a board that genuinely broke
# from one whose batch was killed out from under it.
ABANDONED_MESSAGE = "abandoned: batch terminated"


async def start_run(session: AsyncSession, *, company_slug: str, batch_id: str) -> int:
    """Open an ``in_progress`` run and return its id.

    ``started_at`` is set here rather than accepted as an argument
    because "when did this attempt begin" is not a caller's judgement
    and an injected value could only ever be wrong or a test fixture —
    the reaper's staleness cutoff compares against it.

    Args:
        session: A session with no transaction in progress.
        company_slug: Must already exist in ``companies``.
        batch_id: Groups the runs of one batch so a single invocation's
            results can be queried together.

    Returns:
        The new run's primary key, to be passed to :func:`finish_run` or
        :func:`fail_run`.
    """
    run = CompanyRun(
        company_slug=company_slug,
        batch_id=batch_id,
        status="in_progress",
        started_at=utc_now(),
    )
    async with session.begin():
        session.add(run)
        # Flush and read the id *inside* the transaction. Reading it after
        # the commit would only work under ``expire_on_commit=False`` —
        # SQLAlchemy's default ``True`` expires the instance on commit and
        # turns the attribute access into a lazy refresh against a closed
        # transaction, which raises ``MissingGreenlet`` on the async
        # engine. A repository must be correct under any session factory,
        # not only the one ``engine.create_session_factory`` builds.
        await session.flush()
        run_id: int = run.id

    return run_id


async def finish_run(
    session: AsyncSession, *, run_id: int, url_count: int, verdict: str
) -> None:
    """Mark a run successful.

    *verdict* is stored verbatim from
    :func:`vacantes.extraction.base.compute_verdict`, so drift between
    an extraction's count and its human-counted ``expected_jobs`` is a
    direct query rather than something to recompute later.

    A successful run with ``url_count=0`` and an unhappy verdict is a
    normal outcome, not a failure: the board was reached and honestly
    reported nothing open. Only an exception routes to
    :func:`fail_run`, which keeps retries aimed at genuine breakage
    rather than at empty boards.
    """
    async with session.begin():
        await session.execute(
            update(CompanyRun)
            .where(CompanyRun.id == run_id)
            .values(
                status="success",
                finished_at=utc_now(),
                url_count=url_count,
                verdict=verdict,
            )
        )


async def fail_run(session: AsyncSession, *, run_id: int, error: str) -> None:
    """Mark a run failed, recording *error* for an operator to read.

    ``url_count`` and ``verdict`` are deliberately left null: a failed
    run observed no count, and writing 0 would be indistinguishable
    from a board that was successfully found to be empty.
    """
    async with session.begin():
        await session.execute(
            update(CompanyRun)
            .where(CompanyRun.id == run_id)
            .values(status="failed", finished_at=utc_now(), error_message=error)
        )


async def has_fresh_success(
    session: AsyncSession, *, company_slug: str, since: datetime
) -> bool:
    """Whether *company_slug* succeeded at or after *since*.

    The skip decision's only input. See the module docstring for why
    this counts successes and not attempts.

    Args:
        session: Any session; this is a read and opens no transaction of
            its own.
        company_slug: The company to check.
        since: The freshness cutoff — typically now minus the window.
            Normalized to naive UTC, so an aware value cannot silently
            mis-compare against the stored naive timestamps.

    Returns:
        ``True`` if a successful run finished at or after *since*.
    """
    cutoff = as_naive_utc(since)
    result = await session.execute(
        select(CompanyRun.id)
        .where(
            CompanyRun.company_slug == company_slug,
            CompanyRun.status == "success",
            CompanyRun.finished_at >= cutoff,
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def reap_stale_runs(session: AsyncSession, *, older_than: datetime) -> int:
    """Fail every ``in_progress`` run started before *older_than*.

    Called at batch startup to clean up after a previous batch that was
    killed. The cutoff is on ``started_at`` because an abandoned run has
    no ``finished_at`` to compare — that absence is precisely what
    identifies it.

    Choosing *older_than* is the caller's decision and it is a real
    trade-off: a cutoff shorter than the slowest board's runtime would
    reap a run that is still working. The batch layer derives it from
    the per-company timeout for that reason.

    Args:
        session: A session with no transaction in progress.
        older_than: Runs started strictly before this are reaped.
            Normalized to naive UTC.

    Returns:
        How many rows were reaped.
    """
    cutoff = as_naive_utc(older_than)

    async with session.begin():
        # Select the targets before updating them rather than reading a
        # driver-reported row count afterwards: the ids are exactly the
        # rows this call is responsible for, and both statements share
        # the one transaction so nothing can change status in between.
        stale = await session.execute(
            select(CompanyRun.id).where(
                CompanyRun.status == "in_progress",
                CompanyRun.started_at < cutoff,
            )
        )
        run_ids = list(stale.scalars().all())

        if run_ids:
            await session.execute(
                update(CompanyRun)
                .where(CompanyRun.id.in_(run_ids))
                .values(
                    status="failed",
                    error_message=ABANDONED_MESSAGE,
                    finished_at=utc_now(),
                )
            )

    return len(run_ids)
