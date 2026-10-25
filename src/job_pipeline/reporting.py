"""Single JSON renderer for accepted pipeline results."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from job_pipeline.persistence import jobs_repo, results_repo
from job_pipeline.runner import PipelineResult


async def save_accepted_results(
    result: PipelineResult,
    session_factory: async_sessionmaker[AsyncSession],
    directory: Path,
) -> int:
    directory.mkdir(parents=True, exist_ok=True)
    written = 0
    for outcome in result.outcomes:
        if outcome.status != "completed_eligible":
            continue
        async with session_factory() as session:
            job = await jobs_repo.get(session, outcome.job_id)
            accepted = await results_repo.current(session, outcome.job_id)
        if job is None or accepted is None:
            continue
        payload = json.loads(accepted.result_json)
        envelope = {
            "job_id": job.id,
            "company_slug": job.company_slug,
            "url_key": job.url_key,
            "accepted_at": accepted.accepted_at.isoformat(),
            "extractor_version": accepted.extractor_version,
            "schema_version": accepted.schema_version,
            "result": payload,
        }
        path = directory / f"{job.company_slug}_{job.id}.json"
        path.write_text(
            json.dumps(envelope, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        written += 1
    return written


__all__ = ["save_accepted_results"]
