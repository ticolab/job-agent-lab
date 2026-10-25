"""Reap, observe, plan, fan out, and aggregate pipeline jobs."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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


# Statuses an ordinary run never plans, so a snapshot job in one of them is
# "skipped as completed" in the sense the run summary reports.
COMPLETED_DISPOSITIONS: tuple[str, ...] = (
    "completed_eligible",
    "completed_ineligible",
    "unknown",
    "unavailable",
    "failed",
    "merged",
)


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """What one run did.

    ``skipped`` counts outcomes whose claim was lost to another worker;
    ``skipped_completed`` counts snapshot jobs the planner never selected
    because they already carry a terminal status. The two are different
    questions and the summary prints both.
    """

    run_id: str
    observation: ObservationSummary | None
    outcomes: tuple[JobOutcome, ...]
    reaped: int
    dispositions: Mapping[str, int] | None = None

    def _count(self, status: str) -> int:
        return sum(outcome.status == status for outcome in self.outcomes)

    @property
    def skipped(self) -> int:
        return self._count("skipped")

    @property
    def skipped_completed(self) -> int:
        if self.dispositions is None:
            return 0
        return sum(
            self.dispositions.get(status, 0) for status in COMPLETED_DISPOSITIONS
        )

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
            "skipped_completed": self.skipped_completed,
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
            older_than=now - policy.stale_after,
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
    dispositions: dict[str, int] | None = None
    if reprocess is None:
        observation = await observe(session_factory, companies)
        async with session_factory() as session:
            dispositions = await jobs_repo.disposition_counts(
                session, observation.snapshot_keys, now
            )
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
    # Bound how many jobs are claimed at once. The per-step timeout inside
    # the worker never counts queue time, but a job claimed an hour before
    # it reaches a slot would still carry an attempt old enough for an
    # overlapping run's reaper to mistake for abandoned.
    in_flight = asyncio.Semaphore(policy.in_flight)

    async def _guarded(job_id: int) -> JobOutcome:
        async with in_flight:
            return await process_job(job_id, deps=deps)

    try:
        outcomes = tuple(await asyncio.gather(*(_guarded(job.id) for job in jobs)))
    except BaseException:
        async with session_factory() as session:
            await runs_repo.finish_run(
                session,
                run_id,
                counts={"interrupted": 1},
                finished_at=utc_now(),
            )
        raise
    result = PipelineResult(run_id, observation, outcomes, reaped, dispositions)
    async with session_factory() as session:
        await runs_repo.finish_run(
            session,
            run_id,
            counts=result.counts,
            finished_at=utc_now(),
        )
    return result


__all__ = [
    "COMPLETED_DISPOSITIONS",
    "DepsFactory",
    "PipelineResult",
    "SessionFactory",
    "browser_required",
    "new_run_id",
    "run_pipeline",
]
