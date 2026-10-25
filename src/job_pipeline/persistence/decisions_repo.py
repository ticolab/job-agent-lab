"""Current and historical eligibility decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from job_discovery.persistence.timestamps import as_naive_utc
from job_pipeline.persistence.models import Job, JobDecision


@dataclass(frozen=True)
class DecisionInput:
    decision: str
    reason: str
    evidence_json: str
    policy_version: str


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    id: int
    job_id: int
    attempt_id: int
    content_id: int
    decision: str
    reason: str
    evidence_json: str
    policy_version: str
    is_current: bool
    decided_at: datetime


def _record(decision: JobDecision) -> DecisionRecord:
    return DecisionRecord(
        id=decision.id,
        job_id=decision.job_id,
        attempt_id=decision.attempt_id,
        content_id=decision.content_id,
        decision=decision.decision,
        reason=decision.reason,
        evidence_json=decision.evidence_json,
        policy_version=decision.policy_version,
        is_current=decision.is_current,
        decided_at=decision.decided_at,
    )


async def accept_and_advance(
    session: AsyncSession,
    *,
    job_id: int,
    attempt_id: int,
    content_id: int,
    decision: DecisionInput,
    decided_at: datetime,
) -> int | None:
    moment = as_naive_utc(decided_at)
    async with session.begin():
        owned = await session.execute(
            update(Job)
            .where(
                Job.id == job_id,
                Job.status == "in_progress",
                Job.current_attempt_id == attempt_id,
                Job.current_content_id == content_id,
            )
            .values(next_phase="extraction", updated_at=moment)
            .returning(Job.id)
        )
        if owned.scalar_one_or_none() is None:
            return None
        await session.execute(
            update(JobDecision)
            .where(JobDecision.job_id == job_id, JobDecision.is_current.is_(True))
            .values(is_current=False)
        )
        row = JobDecision(
            job_id=job_id,
            attempt_id=attempt_id,
            content_id=content_id,
            decision=decision.decision,
            reason=decision.reason,
            evidence_json=decision.evidence_json,
            policy_version=decision.policy_version,
            is_current=True,
            decided_at=moment,
        )
        session.add(row)
        await session.flush()
        decision_id: int = row.id
    return decision_id


async def accept_and_complete(
    session: AsyncSession,
    *,
    job_id: int,
    attempt_id: int,
    content_id: int,
    decision: DecisionInput,
    status: str,
    decided_at: datetime,
) -> int | None:
    moment = as_naive_utc(decided_at)
    async with session.begin():
        owned = await session.execute(
            update(Job)
            .where(
                Job.id == job_id,
                Job.status == "in_progress",
                Job.current_attempt_id == attempt_id,
                Job.current_content_id == content_id,
            )
            .values(
                status=status,
                next_phase="eligibility",
                next_attempt_after=None,
                current_attempt_id=None,
                updated_at=moment,
            )
            .returning(Job.id)
        )
        if owned.scalar_one_or_none() is None:
            return None
        await session.execute(
            update(JobDecision)
            .where(JobDecision.job_id == job_id, JobDecision.is_current.is_(True))
            .values(is_current=False)
        )
        row = JobDecision(
            job_id=job_id,
            attempt_id=attempt_id,
            content_id=content_id,
            decision=decision.decision,
            reason=decision.reason,
            evidence_json=decision.evidence_json,
            policy_version=decision.policy_version,
            is_current=True,
            decided_at=moment,
        )
        session.add(row)
        await session.flush()
        decision_id: int = row.id
    return decision_id


async def accept(
    session: AsyncSession,
    *,
    job_id: int,
    attempt_id: int,
    content_id: int,
    decision: DecisionInput,
    decided_at: datetime,
    require_ownership: bool = False,
) -> int | None:
    async with session.begin():
        if require_ownership:
            owned = await session.execute(
                select(Job.id).where(
                    Job.id == job_id,
                    Job.status == "in_progress",
                    Job.current_attempt_id == attempt_id,
                    Job.current_content_id == content_id,
                )
            )
            if owned.scalar_one_or_none() is None:
                return None
        await session.execute(
            update(JobDecision)
            .where(JobDecision.job_id == job_id, JobDecision.is_current.is_(True))
            .values(is_current=False)
        )
        row = JobDecision(
            job_id=job_id,
            attempt_id=attempt_id,
            content_id=content_id,
            decision=decision.decision,
            reason=decision.reason,
            evidence_json=decision.evidence_json,
            policy_version=decision.policy_version,
            is_current=True,
            decided_at=as_naive_utc(decided_at),
        )
        session.add(row)
        await session.flush()
        decision_id: int = row.id
    return decision_id


async def current(session: AsyncSession, job_id: int) -> DecisionRecord | None:
    result = await session.execute(
        select(JobDecision).where(
            JobDecision.job_id == job_id,
            JobDecision.is_current.is_(True),
        )
    )
    decision = result.scalar_one_or_none()
    return _record(decision) if decision is not None else None


async def invalidate_current(session: AsyncSession, job_id: int) -> None:
    async with session.begin():
        await session.execute(
            update(JobDecision)
            .where(JobDecision.job_id == job_id, JobDecision.is_current.is_(True))
            .values(is_current=False)
        )


async def history(session: AsyncSession, job_id: int) -> tuple[DecisionRecord, ...]:
    result = await session.execute(
        select(JobDecision)
        .where(JobDecision.job_id == job_id)
        .order_by(JobDecision.decided_at, JobDecision.id)
    )
    return tuple(_record(decision) for decision in result.scalars())
