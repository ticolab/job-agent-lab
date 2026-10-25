"""Pipeline run lifecycle and interrupted-attempt recovery."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from job_discovery.persistence.timestamps import as_naive_utc
from job_pipeline.persistence.models import Job, JobAttempt, PipelineRun


async def start_run(
    session: AsyncSession,
    *,
    run_id: str,
    selection: str,
    model: str,
    started_at: datetime,
) -> None:
    async with session.begin():
        session.add(
            PipelineRun(
                id=run_id,
                selection=selection,
                model=model,
                started_at=as_naive_utc(started_at),
            )
        )


async def finish_run(
    session: AsyncSession,
    run_id: str,
    *,
    counts: Mapping[str, int],
    finished_at: datetime,
) -> None:
    async with session.begin():
        await session.execute(
            update(PipelineRun)
            .where(PipelineRun.id == run_id)
            .values(
                counts_json=json.dumps(dict(counts), sort_keys=True),
                finished_at=as_naive_utc(finished_at),
            )
        )


async def reap_stale(
    session: AsyncSession,
    *,
    older_than: datetime,
    finished_at: datetime,
) -> int:
    cutoff = as_naive_utc(older_than)
    ended = as_naive_utc(finished_at)
    async with session.begin():
        stale_result = await session.execute(
            select(JobAttempt.id, JobAttempt.job_id)
            .join(
                Job,
                (Job.id == JobAttempt.job_id)
                & (Job.current_attempt_id == JobAttempt.id),
            )
            .where(
                JobAttempt.finished_at.is_(None),
                JobAttempt.started_at < cutoff,
            )
        )
        stale = tuple(stale_result)
        if not stale:
            return 0
        attempt_ids = tuple(row.id for row in stale)
        await session.execute(
            update(JobAttempt)
            .where(JobAttempt.id.in_(attempt_ids))
            .values(
                outcome="interrupted",
                error_category="interrupted",
                error_message="pipeline run interrupted",
                finished_at=ended,
            )
        )
        for attempt_id, job_id in stale:
            await session.execute(
                update(Job)
                .where(
                    Job.id == job_id,
                    Job.status == "in_progress",
                    Job.current_attempt_id == attempt_id,
                )
                .values(
                    status="retry",
                    next_attempt_after=ended,
                    current_attempt_id=None,
                    reprocess_requested=False,
                    updated_at=ended,
                )
            )
    return len(stale)
