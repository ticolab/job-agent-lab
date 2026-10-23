"""Attempt lifecycle and history for one job."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from job_discovery.persistence.timestamps import as_naive_utc
from job_pipeline.persistence.models import Job, JobAttempt


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    id: int
    job_id: int
    run_id: str
    trigger: str
    reason: str | None
    phase_reached: str | None
    outcome: str | None
    error_category: str | None
    error_message: str | None
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    policy_version: str
    extractor_version: str
    started_at: datetime
    finished_at: datetime | None


def _record(attempt: JobAttempt) -> AttemptRecord:
    return AttemptRecord(
        id=attempt.id,
        job_id=attempt.job_id,
        run_id=attempt.run_id,
        trigger=attempt.trigger,
        reason=attempt.reason,
        phase_reached=attempt.phase_reached,
        outcome=attempt.outcome,
        error_category=attempt.error_category,
        error_message=attempt.error_message,
        model=attempt.model,
        prompt_tokens=attempt.prompt_tokens,
        completion_tokens=attempt.completion_tokens,
        policy_version=attempt.policy_version,
        extractor_version=attempt.extractor_version,
        started_at=attempt.started_at,
        finished_at=attempt.finished_at,
    )


async def claim_and_start(
    session: AsyncSession,
    *,
    job_id: int,
    run_id: str,
    trigger: str,
    reason: str | None,
    model: str,
    policy_version: str,
    extractor_version: str,
    started_at: datetime,
) -> int | None:
    eligible = (
        (Job.status == "retry")
        & Job.reprocess_requested.is_(True)
        & (Job.next_attempt_after <= as_naive_utc(started_at))
        if trigger == "reprocess"
        else Job.status.in_(("new", "retry"))
        & Job.reprocess_requested.is_(False)
        & ((Job.status == "new") | (Job.next_attempt_after <= as_naive_utc(started_at)))
    )

    moment = as_naive_utc(started_at)
    async with session.begin():
        claimed = await session.execute(
            update(Job)
            .where(Job.id == job_id, eligible)
            .values(
                status="in_progress",
                attempt_count=Job.attempt_count + 1,
                next_attempt_after=None,
                current_attempt_id=None,
                reprocess_requested=False,
                updated_at=moment,
            )
            .returning(Job.id)
        )
        if claimed.scalar_one_or_none() is None:
            return None
        attempt = JobAttempt(
            job_id=job_id,
            run_id=run_id,
            trigger=trigger,
            reason=reason,
            model=model,
            policy_version=policy_version,
            extractor_version=extractor_version,
            started_at=moment,
        )
        session.add(attempt)
        await session.flush()
        attempt_id: int = attempt.id
        owned = await session.execute(
            update(Job)
            .where(
                Job.id == job_id,
                Job.status == "in_progress",
                Job.current_attempt_id.is_(None),
            )
            .values(current_attempt_id=attempt_id)
            .returning(Job.id)
        )
        if owned.scalar_one_or_none() is None:
            raise RuntimeError(f"could not establish ownership for job {job_id}")
    return attempt_id


async def start(
    session: AsyncSession,
    *,
    job_id: int,
    run_id: str,
    trigger: str,
    reason: str | None,
    model: str,
    policy_version: str,
    extractor_version: str,
    started_at: datetime,
) -> int:
    attempt = JobAttempt(
        job_id=job_id,
        run_id=run_id,
        trigger=trigger,
        reason=reason,
        model=model,
        policy_version=policy_version,
        extractor_version=extractor_version,
        started_at=as_naive_utc(started_at),
    )
    async with session.begin():
        session.add(attempt)
        await session.flush()
        attempt_id: int = attempt.id
    return attempt_id


async def finish(
    session: AsyncSession,
    attempt_id: int,
    *,
    phase_reached: str,
    outcome: str,
    finished_at: datetime,
    error_category: str | None = None,
    error_message: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    require_ownership: bool = False,
) -> bool:
    async with session.begin():
        statement = update(JobAttempt).where(
            JobAttempt.id == attempt_id,
            JobAttempt.finished_at.is_(None),
        )
        if require_ownership:
            statement = statement.where(
                select(Job.current_attempt_id)
                .where(Job.id == JobAttempt.job_id)
                .scalar_subquery()
                == attempt_id
            )
        result = await session.execute(
            statement.values(
                phase_reached=phase_reached,
                outcome=outcome,
                error_category=error_category,
                error_message=error_message,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                finished_at=as_naive_utc(finished_at),
            ).returning(JobAttempt.id)
        )
    return result.scalar_one_or_none() is not None


async def fail(
    session: AsyncSession,
    attempt_id: int,
    *,
    phase_reached: str,
    error_category: str,
    error_message: str,
    finished_at: datetime,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> None:
    await finish(
        session,
        attempt_id,
        phase_reached=phase_reached,
        outcome="failed",
        error_category=error_category,
        error_message=error_message,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        finished_at=finished_at,
    )


async def last_error_category(session: AsyncSession, job_id: int) -> str | None:
    result = await session.execute(
        select(JobAttempt.error_category)
        .where(JobAttempt.job_id == job_id, JobAttempt.error_category.is_not(None))
        .order_by(JobAttempt.started_at.desc(), JobAttempt.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def has_last_error(
    session: AsyncSession, job_ids: tuple[int, ...], category: str
) -> bool:
    if not job_ids:
        return False
    latest = (
        select(
            JobAttempt.job_id,
            func.max(JobAttempt.id).label("attempt_id"),
        )
        .join(Job, Job.id == JobAttempt.job_id)
        .where(
            JobAttempt.job_id.in_(job_ids),
            Job.status != "merged",
            Job.merged_into_id.is_(None),
        )
        .group_by(JobAttempt.job_id)
        .subquery()
    )
    result = await session.execute(
        select(JobAttempt.id)
        .join(latest, JobAttempt.id == latest.c.attempt_id)
        .where(JobAttempt.error_category == category)
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def history(session: AsyncSession, job_id: int) -> tuple[AttemptRecord, ...]:
    result = await session.execute(
        select(JobAttempt)
        .where(JobAttempt.job_id == job_id)
        .order_by(JobAttempt.started_at, JobAttempt.id)
    )
    return tuple(_record(attempt) for attempt in result.scalars())
