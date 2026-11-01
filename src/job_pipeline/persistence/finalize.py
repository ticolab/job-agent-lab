from datetime import datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from job_discovery.persistence.timestamps import as_naive_utc
from job_pipeline.persistence.models import JobAttempt


class CheckpointError(RuntimeError):
    pass


async def finish_owned_attempt(
    session: AsyncSession,
    *,
    job_id: int,
    attempt_id: int,
    outcome: str,
    phase_reached: str,
    finished_at: datetime,
    error_category: str | None = None,
    error_message: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> None:
    """Run inside the caller's transaction, after a lease-guarded job update."""
    finished = await session.execute(
        update(JobAttempt)
        .where(
            JobAttempt.id == attempt_id,
            JobAttempt.job_id == job_id,
            JobAttempt.finished_at.is_(None),
        )
        .values(
            outcome=outcome,
            phase_reached=phase_reached,
            error_category=error_category,
            error_message=error_message,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            finished_at=as_naive_utc(finished_at),
        )
        .returning(JobAttempt.id)
    )
    if finished.scalar_one_or_none() is None:
        raise CheckpointError(f"job {job_id} has no unfinished owned attempt")
