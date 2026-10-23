"""Idempotent storage and retrieval of acquired posting content."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from job_discovery.persistence.timestamps import as_naive_utc
from job_pipeline.persistence.models import Job, JobContent


@dataclass(frozen=True)
class AcquiredContent:
    content_hash: str
    fingerprint: str
    requisition_id: str | None
    resolved_url: str
    http_status: int | None
    method: str
    title: str | None
    text: str
    jsonld_json: str | None
    retrieved_at: datetime


@dataclass(frozen=True, slots=True)
class ContentRecord:
    id: int
    job_id: int
    content_hash: str
    fingerprint: str
    requisition_id: str | None
    resolved_url: str
    http_status: int | None
    method: str
    title: str | None
    text: str
    jsonld_json: str | None
    retrieved_at: datetime


def _record(content: JobContent) -> ContentRecord:
    return ContentRecord(
        id=content.id,
        job_id=content.job_id,
        content_hash=content.content_hash,
        fingerprint=content.fingerprint,
        requisition_id=content.requisition_id,
        resolved_url=content.resolved_url,
        http_status=content.http_status,
        method=content.method,
        title=content.title,
        text=content.text,
        jsonld_json=content.jsonld_json,
        retrieved_at=content.retrieved_at,
    )


async def store(session: AsyncSession, job_id: int, acquired: AcquiredContent) -> int:
    async with session.begin():
        result = await session.execute(
            select(JobContent.id).where(
                JobContent.job_id == job_id,
                JobContent.content_hash == acquired.content_hash,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing
        content = JobContent(
            job_id=job_id,
            content_hash=acquired.content_hash,
            fingerprint=acquired.fingerprint,
            requisition_id=acquired.requisition_id,
            resolved_url=acquired.resolved_url,
            http_status=acquired.http_status,
            method=acquired.method,
            title=acquired.title,
            text=acquired.text,
            jsonld_json=acquired.jsonld_json,
            retrieved_at=as_naive_utc(acquired.retrieved_at),
        )
        session.add(content)
        await session.flush()
        content_id: int = content.id
    return content_id


async def store_checkpoint(
    session: AsyncSession,
    job_id: int,
    attempt_id: int,
    acquired: AcquiredContent,
) -> int | None:
    async with session.begin():
        result = await session.execute(
            select(JobContent.id).where(
                JobContent.job_id == job_id,
                JobContent.content_hash == acquired.content_hash,
            )
        )
        content_id = result.scalar_one_or_none()
        if content_id is None:
            content = JobContent(
                job_id=job_id,
                content_hash=acquired.content_hash,
                fingerprint=acquired.fingerprint,
                requisition_id=acquired.requisition_id,
                resolved_url=acquired.resolved_url,
                http_status=acquired.http_status,
                method=acquired.method,
                title=acquired.title,
                text=acquired.text,
                jsonld_json=acquired.jsonld_json,
                retrieved_at=as_naive_utc(acquired.retrieved_at),
            )
            session.add(content)
            await session.flush()
            content_id = content.id
        owned = await session.execute(
            update(Job)
            .where(
                Job.id == job_id,
                Job.status == "in_progress",
                Job.current_attempt_id == attempt_id,
            )
            .values(current_content_id=content_id)
            .returning(Job.id)
        )
        if owned.scalar_one_or_none() is None:
            return None
    return content_id


async def current(session: AsyncSession, job_id: int) -> ContentRecord | None:
    result = await session.execute(
        select(JobContent)
        .join(Job, Job.current_content_id == JobContent.id)
        .where(Job.id == job_id, JobContent.job_id == job_id)
    )
    content = result.scalar_one_or_none()
    return _record(content) if content is not None else None


async def latest(session: AsyncSession, job_id: int) -> ContentRecord | None:
    result = await session.execute(
        select(JobContent)
        .where(JobContent.job_id == job_id)
        .order_by(JobContent.retrieved_at.desc(), JobContent.id.desc())
        .limit(1)
    )
    content = result.scalar_one_or_none()
    return _record(content) if content is not None else None


async def history(session: AsyncSession, job_id: int) -> tuple[ContentRecord, ...]:
    result = await session.execute(
        select(JobContent)
        .where(JobContent.job_id == job_id)
        .order_by(JobContent.retrieved_at, JobContent.id)
    )
    return tuple(_record(content) for content in result.scalars())
