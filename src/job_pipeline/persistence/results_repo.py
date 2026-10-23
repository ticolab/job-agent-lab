"""Current and historical accepted extraction results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from job_discovery.persistence.timestamps import as_naive_utc
from job_pipeline.persistence.models import Job, JobResult


@dataclass(frozen=True)
class ResultInput:
    extractor_version: str
    schema_version: int
    result_json: str


@dataclass(frozen=True, slots=True)
class ResultRecord:
    id: int
    job_id: int
    attempt_id: int
    content_id: int
    extractor_version: str
    schema_version: int
    result_json: str
    is_current: bool
    accepted_at: datetime


def _record(result: JobResult) -> ResultRecord:
    return ResultRecord(
        id=result.id,
        job_id=result.job_id,
        attempt_id=result.attempt_id,
        content_id=result.content_id,
        extractor_version=result.extractor_version,
        schema_version=result.schema_version,
        result_json=result.result_json,
        is_current=result.is_current,
        accepted_at=result.accepted_at,
    )


async def accept_and_complete(
    session: AsyncSession,
    *,
    job_id: int,
    attempt_id: int,
    content_id: int,
    result: ResultInput,
    accepted_at: datetime,
) -> int | None:
    moment = as_naive_utc(accepted_at)
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
                status="completed_eligible",
                next_phase="extraction",
                next_attempt_after=None,
                current_attempt_id=None,
                updated_at=moment,
            )
            .returning(Job.id)
        )
        if owned.scalar_one_or_none() is None:
            return None
        await session.execute(
            update(JobResult)
            .where(JobResult.job_id == job_id, JobResult.is_current.is_(True))
            .values(is_current=False)
        )
        row = JobResult(
            job_id=job_id,
            attempt_id=attempt_id,
            content_id=content_id,
            extractor_version=result.extractor_version,
            schema_version=result.schema_version,
            result_json=result.result_json,
            is_current=True,
            accepted_at=moment,
        )
        session.add(row)
        await session.flush()
        result_id: int = row.id
    return result_id


async def accept(
    session: AsyncSession,
    *,
    job_id: int,
    attempt_id: int,
    content_id: int,
    result: ResultInput,
    accepted_at: datetime,
) -> int:
    async with session.begin():
        await session.execute(
            update(JobResult)
            .where(JobResult.job_id == job_id, JobResult.is_current.is_(True))
            .values(is_current=False)
        )
        row = JobResult(
            job_id=job_id,
            attempt_id=attempt_id,
            content_id=content_id,
            extractor_version=result.extractor_version,
            schema_version=result.schema_version,
            result_json=result.result_json,
            is_current=True,
            accepted_at=as_naive_utc(accepted_at),
        )
        session.add(row)
        await session.flush()
        result_id: int = row.id
    return result_id


async def current(session: AsyncSession, job_id: int) -> ResultRecord | None:
    query = select(JobResult).where(
        JobResult.job_id == job_id,
        JobResult.is_current.is_(True),
    )
    result = await session.execute(query)
    row = result.scalar_one_or_none()
    return _record(row) if row is not None else None


async def invalidate_current(session: AsyncSession, job_id: int) -> None:
    async with session.begin():
        await session.execute(
            update(JobResult)
            .where(JobResult.job_id == job_id, JobResult.is_current.is_(True))
            .values(is_current=False)
        )


async def history(session: AsyncSession, job_id: int) -> tuple[ResultRecord, ...]:
    result = await session.execute(
        select(JobResult)
        .where(JobResult.job_id == job_id)
        .order_by(JobResult.accepted_at, JobResult.id)
    )
    return tuple(_record(row) for row in result.scalars())
