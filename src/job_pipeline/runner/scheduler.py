"""Reap, observe, plan, fan out, and aggregate pipeline jobs."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal, Protocol
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from job_discovery.domain.company import Company
from job_discovery.persistence import catalog_repo
from job_discovery.persistence.timestamps import utc_now
from job_pipeline.persistence import attempts_repo, jobs_repo, runs_repo
from job_pipeline.policy import PipelinePolicy
from job_pipeline.runner.observe import ObservationSummary, observe
from job_pipeline.runner.worker import JobOutcome, WorkerDeps, process_job

SessionFactory = async_sessionmaker[AsyncSession]


class DepsFactory(Protocol):
    def __call__(
        self,
        run_id: str,
        trigger: Literal["scheduled", "reprocess"],
        reason: str | None,
        http_semaphore: asyncio.Semaphore,
        browser_semaphore: asyncio.Semaphore,
        model_semaphore: asyncio.Semaphore,
        per_host_semaphores: dict[str, asyncio.Semaphore],
        per_host_lock: asyncio.Lock,
    ) -> WorkerDeps: ...


def new_run_id() -> str:
    return f"{utc_now():%Y%m%d_%H%M%S}_{uuid4().hex[:8]}"


@dataclass(frozen=True, slots=True)
class PipelineResult:
    run_id: str
    observation: ObservationSummary | None
    outcomes: tuple[JobOutcome, ...]
    reaped: int

    def _count(self, status: str) -> int:
        return sum(outcome.status == status for outcome in self.outcomes)

    @property
    def skipped(self) -> int:
        return self._count("skipped")

    @property
    def skipped_completed(self) -> int:
        return self.skipped

    @property
    def attempted(self) -> int:
        return len(self.outcomes) - self.skipped

    @property
    def eligible(self) -> int:
        return self._count("completed_eligible")

    @property
    def ineligible(self) -> int:
        return self._count("completed_ineligible")

    @property
    def unknown(self) -> int:
        return self._count("unknown")

    @property
    def unavailable(self) -> int:
        return self._count("unavailable")

    @property
    def retry(self) -> int:
        return self._count("retry")

    @property
    def failed(self) -> int:
        return self._count("failed")

    @property
    def merged(self) -> int:
        return self._count("merged")

    @property
    def interrupted(self) -> int:
        return self.reaped

    @property
    def prompt_tokens(self) -> int:
        return sum(outcome.tokens.prompt for outcome in self.outcomes)

    @property
    def completion_tokens(self) -> int:
        return sum(outcome.tokens.completion for outcome in self.outcomes)

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def counts(self) -> dict[str, int]:
        return {
            "skipped": self.skipped,
            "attempted": self.attempted,
            "eligible": self.eligible,
            "ineligible": self.ineligible,
            "unknown": self.unknown,
            "unavailable": self.unavailable,
            "retry": self.retry,
            "failed": self.failed,
            "merged": self.merged,
            "interrupted": self.interrupted,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }


async def browser_required(
    companies: Sequence[Company],
    jobs: Sequence[jobs_repo.JobRecord],
    session_factory: SessionFactory,
) -> bool:
    if any(company.strategy == "dom" for company in companies):
        return True
    async with session_factory() as session:
        return await attempts_repo.has_last_error(
            session, tuple(job.id for job in jobs), "empty_content"
        )


async def run_pipeline(
    companies: Sequence[Company],
    *,
    session_factory: SessionFactory,
    deps_factory: DepsFactory,
    policy: PipelinePolicy,
    reprocess: Sequence[int] | None = None,
    reason: str | None = None,
) -> PipelineResult:
    run_id = new_run_id()
    now = utc_now()
    async with session_factory() as session:
        await catalog_repo.sync_catalog(session, companies)
    async with session_factory() as session:
        reaped = await runs_repo.reap_stale(
            session,
            older_than=now - timedelta(seconds=policy.job_timeout),
            finished_at=now,
        )
    selection = ",".join(company.slug for company in companies)
    async with session_factory() as session:
        await runs_repo.start_run(
            session,
            run_id=run_id,
            selection=selection,
            model=policy.model,
            started_at=now,
        )
    observation: ObservationSummary | None = None
    if reprocess is None:
        observation = await observe(session_factory, companies)
        async with session_factory() as session:
            jobs = await jobs_repo.list_work(
                session,
                observation.snapshot_keys,
                now,
                policy.limit,
            )
        trigger: Literal["scheduled", "reprocess"] = "scheduled"
    else:
        exact_ids = tuple(dict.fromkeys(reprocess))
        async with session_factory() as session:
            selected = await jobs_repo.list_by_ids(session, exact_ids)
        by_id = {job.id: job for job in selected}
        jobs = tuple(by_id[job_id] for job_id in exact_ids if job_id in by_id)
        trigger = "reprocess"

    http_semaphore = asyncio.Semaphore(policy.http_concurrency)
    browser_semaphore = asyncio.Semaphore(policy.browser_concurrency)
    model_semaphore = asyncio.Semaphore(policy.model_concurrency)
    per_host_semaphores: dict[str, asyncio.Semaphore] = {}
    per_host_lock = asyncio.Lock()
    deps = deps_factory(
        run_id,
        trigger,
        reason,
        http_semaphore,
        browser_semaphore,
        model_semaphore,
        per_host_semaphores,
        per_host_lock,
    )
    try:
        outcomes = tuple(
            await asyncio.gather(*(process_job(job.id, deps=deps) for job in jobs))
        )
    except BaseException:
        async with session_factory() as session:
            await runs_repo.finish_run(
                session,
                run_id,
                counts={"interrupted": 1},
                finished_at=utc_now(),
            )
        raise
    result = PipelineResult(run_id, observation, outcomes, reaped)
    async with session_factory() as session:
        await runs_repo.finish_run(
            session,
            run_id,
            counts=result.counts,
            finished_at=utc_now(),
        )
    return result


__all__ = [
    "DepsFactory",
    "PipelineResult",
    "SessionFactory",
    "browser_required",
    "new_run_id",
    "run_pipeline",
]
