from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import TypeVar

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from job_discovery.persistence import jobs_repo as discovery_jobs
from job_discovery.persistence.engine import database
from job_discovery.persistence.timestamps import utc_now
from job_pipeline.content.model import AcquiredContent
from job_pipeline.llm import Completion
from job_pipeline.persistence import (
    attempts_repo,
    contents_repo,
    decisions_repo,
    jobs_repo,
)
from job_pipeline.persistence.models import Job, JobAttempt, PipelineRun, Stage
from job_pipeline.policy import PipelinePolicy
from job_pipeline.runner import scheduler
from job_pipeline.runner.observe import read_snapshot_keys
from job_pipeline.runner.worker import WorkerDeps
from tests.unit.asyncio_harness import run_async, track_engine
from tests.unit.test_pipeline_cli import COMPANY, URL, _seed_audit
from tests.unit.test_pipeline_worker import POSTING_TEXT, EligibleCaller

T = TypeVar("T", bound=BaseModel)
Factory = async_sessionmaker[AsyncSession]


async def _snapshot(factory: Factory, urls: Sequence[str]) -> None:
    async with factory() as session:
        await discovery_jobs.replace_company_urls(
            session, company_slug=COMPANY.slug, urls=urls, captured_at=utc_now()
        )


async def _ready(factory: Factory, url: str) -> int:
    async with factory() as session:
        await jobs_repo.observe(session, COMPANY.slug, [(url, url)], utc_now())
    async with factory() as session:
        job = (await jobs_repo.list_by_url(session, url))[0]
    async with factory() as session:
        attempt_id = await attempts_repo.start(
            session,
            job_id=job.id,
            run_id="seed",
            trigger="scheduled",
            reason=None,
            model="test",
            policy_version="test",
            extractor_version="test",
            started_at=utc_now(),
        )
    async with factory() as session:
        content_id = await contents_repo.store(
            session,
            job.id,
            contents_repo.AcquiredContent(
                content_hash=f"hash-{job.id}",
                fingerprint=f"fingerprint-{job.id}",
                requisition_id=None,
                resolved_url=url,
                http_status=200,
                method="main_content",
                title="Software Engineer",
                text=POSTING_TEXT,
                jsonld_json=None,
                retrieved_at=utc_now(),
            ),
        )
    async with factory() as session:
        await decisions_repo.accept(
            session,
            job_id=job.id,
            attempt_id=attempt_id,
            content_id=content_id,
            decision=decisions_repo.DecisionInput(
                "eligible", "Costa Rica", "[]", "test"
            ),
            decided_at=utc_now(),
        )
    async with factory() as session:
        await attempts_repo.finish(
            session,
            attempt_id,
            phase_reached="eligibility",
            outcome="ready_for_extraction",
            finished_at=utc_now(),
        )
    async with factory() as session, session.begin():
        row = await session.get(Job, job.id)
        assert row is not None
        row.current_content_id = content_id
        row.status = "ready_for_extraction"
        row.next_phase = "extraction"
    return job.id


def _deps(
    factory: Factory,
    stage: Stage,
    caller: EligibleCaller,
    client: httpx.AsyncClient | None = None,
) -> scheduler.DepsFactory:
    def build(
        run_id: str,
        stage: Stage,
        trigger: str,
        reason: str | None,
        http_semaphore: asyncio.Semaphore,
        browser_semaphore: asyncio.Semaphore,
        model_semaphore: asyncio.Semaphore,
        per_host_semaphores: dict[str, asyncio.Semaphore],
        per_host_lock: asyncio.Lock,
    ) -> WorkerDeps:
        from typing import Literal, cast

        return WorkerDeps(
            session_factory=factory,
            stage=stage,
            http_client=client,
            browser=None,
            caller=caller,
            policy=PipelinePolicy(model="test"),
            run_id=run_id,
            trigger=cast(Literal["scheduled", "reprocess"], trigger),
            reason=reason,
            companies={COMPANY.slug: COMPANY},
            http_semaphore=http_semaphore,
            browser_semaphore=browser_semaphore,
            model_semaphore=model_semaphore,
            per_host_semaphores=per_host_semaphores,
            per_host_lock=per_host_lock,
        )

    return build


def test_overlapping_stages_use_separate_engines_and_disjoint_leases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        path = tmp_path / "overlap.db"
        factory, _ = await _seed_audit(path)
        ready_id = await _ready(factory, f"{URL}0")
        fresh_url = f"{URL}1"
        await _snapshot(factory, [f"{URL}0", fresh_url])
        entered: set[Stage] = set()
        both = asyncio.Event()
        inspected = asyncio.Barrier(2)

        class GatedCaller(EligibleCaller):
            async def complete(
                self,
                messages: Sequence[Mapping[str, str]],
                schema: type[T],
            ) -> Completion[T]:
                from job_eligibility import EligibilityResponse

                stage: Stage = (
                    "eligibility" if schema is EligibilityResponse else "extraction"
                )
                entered.add(stage)
                if len(entered) == 2:
                    both.set()
                await asyncio.wait_for(both.wait(), 5)
                async with factory() as session:
                    unfinished = (
                        (
                            await session.execute(
                                select(JobAttempt).where(
                                    JobAttempt.finished_at.is_(None)
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                ids = [attempt.job_id for attempt in unfinished]
                assert len(ids) == len(set(ids)) == 2
                await asyncio.wait_for(inspected.wait(), 5)
                return await super().complete(messages, schema)

        async def acquire(*args: object, **kwargs: object) -> AcquiredContent:
            return AcquiredContent(
                POSTING_TEXT,
                "Software Engineer",
                fresh_url,
                200,
                "main_content",
                None,
                None,
                utc_now(),
                "fresh-hash",
            )

        monkeypatch.setattr("job_pipeline.runner.worker.acquire", acquire)
        async with database(path) as first, database(path) as second:
            track_engine(first.kw["bind"])
            track_engine(second.kw["bind"])
            assert first.kw["bind"] is not second.kw["bind"]
            caller = GatedCaller()
            async with httpx.AsyncClient() as client:
                results = await asyncio.gather(
                    scheduler.run_pipeline(
                        [COMPANY],
                        stage="eligibility",
                        session_factory=first,
                        deps_factory=_deps(first, "eligibility", caller, client),
                        policy=PipelinePolicy(model="test"),
                    ),
                    scheduler.run_pipeline(
                        [COMPANY],
                        stage="extraction",
                        session_factory=second,
                        deps_factory=_deps(second, "extraction", caller),
                        policy=PipelinePolicy(model="test"),
                    ),
                )
            assert entered == {"eligibility", "extraction"}
            assert sorted(
                outcome.status for result in results for outcome in result.outcomes
            ) == [
                "completed_eligible",
                "ready_for_extraction",
            ]
            async with first() as session:
                rows = (
                    await session.execute(
                        select(JobAttempt, PipelineRun)
                        .join(PipelineRun)
                        .where(PipelineRun.id != "seed")
                    )
                ).all()
                ready_job = await jobs_repo.get(session, ready_id)
            assert ready_job is not None and ready_job.status == "completed_eligible"
            for attempt, run in rows:
                assert attempt.finished_at is not None
                assert attempt.phase_reached == run.stage

    run_async(scenario())


@pytest.mark.parametrize("stage", ["eligibility", "extraction"])
def test_cross_stage_reaping_and_legacy_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: Stage,
) -> None:
    async def scenario() -> None:
        path = tmp_path / "recovery.db"
        factory, _ = await _seed_audit(path)
        job_id = await _ready(factory, f"{URL}0")
        await _snapshot(factory, [f"{URL}0"])
        if stage == "eligibility":
            async with factory() as session, session.begin():
                row = await session.get(Job, job_id)
                assert row is not None
                row.status = "retry"
                row.next_phase = "eligibility"
                row.next_attempt_after = utc_now() - timedelta(seconds=20)
        async with factory() as session:
            attempt_id = await attempts_repo.claim_and_start(
                session,
                job_id=job_id,
                run_id="seed",
                stage=stage,
                trigger="scheduled",
                reason=None,
                model="test",
                policy_version="test",
                extractor_version="test",
                started_at=utc_now() - timedelta(seconds=10),
            )
        assert attempt_id is not None
        other: Stage = "extraction" if stage == "eligibility" else "eligibility"
        async with database(path) as second:
            track_engine(second.kw["bind"])
            result = await scheduler.run_pipeline(
                [COMPANY],
                stage=other,
                session_factory=second,
                deps_factory=_deps(second, other, EligibleCaller()),
                policy=PipelinePolicy(model="test", job_timeout=1),
            )
            assert result.reaped == 0
            async with factory() as session:
                attempt = await session.get(JobAttempt, attempt_id)
                assert attempt is not None and attempt.finished_at is None
            async with httpx.AsyncClient() as client:
                result = await scheduler.run_pipeline(
                    [COMPANY],
                    stage=stage,
                    session_factory=second,
                    deps_factory=_deps(second, stage, EligibleCaller(), client),
                    policy=PipelinePolicy(model="test", job_timeout=1),
                )
            assert result.reaped == 1
            assert [outcome.status for outcome in result.outcomes] == [
                "ready_for_extraction"
                if stage == "eligibility"
                else "completed_eligible"
            ]
            async with factory() as session:
                attempt = await session.get(JobAttempt, attempt_id)
                assert attempt is not None and attempt.outcome == "interrupted"

    run_async(scenario())


def test_extraction_read_only_membership_exclusions_and_reappearance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("extraction must not observe or sync")

    async def scenario() -> None:
        path = tmp_path / "selection.db"
        factory, _ = await _seed_audit(path)
        inside = await _ready(factory, f"{URL}0")
        outside = await _ready(factory, f"{URL}1")
        alias = await _ready(factory, f"{URL}2")
        async with factory() as session, session.begin():
            row = await session.get(Job, alias)
            assert row is not None
            row.status = "merged"
            row.merged_into_id = inside
        await _snapshot(factory, [f"{URL}2"])
        async with factory() as session:
            before = {
                table.name: len((await session.execute(select(table))).all())
                for table in Job.metadata.sorted_tables
            }
        keys = await read_snapshot_keys(factory, [COMPANY])
        assert keys == frozenset({(COMPANY.slug, f"{URL}0")})
        async with factory() as session:
            after = {
                table.name: len((await session.execute(select(table))).all())
                for table in Job.metadata.sorted_tables
            }
        assert before == after
        monkeypatch.setattr(scheduler, "observe", forbidden)
        monkeypatch.setattr(scheduler.catalog_repo, "sync_catalog", forbidden)
        caller = EligibleCaller()
        result = await scheduler.run_pipeline(
            [COMPANY],
            stage="extraction",
            session_factory=factory,
            deps_factory=_deps(factory, "extraction", caller),
            policy=PipelinePolicy(model="test"),
        )
        assert [outcome.job_id for outcome in result.outcomes] == [inside]
        async with factory() as session:
            job = await jobs_repo.get(session, outside)
            assert job is not None and job.status == "ready_for_extraction"
            merged = await jobs_repo.get(session, alias)
            assert merged is not None and merged.status == "merged"
        await _snapshot(factory, [f"{URL}1"])
        result = await scheduler.run_pipeline(
            [COMPANY],
            stage="extraction",
            session_factory=factory,
            deps_factory=_deps(factory, "extraction", caller),
            policy=PipelinePolicy(model="test"),
        )
        assert [outcome.job_id for outcome in result.outcomes] == [outside]
        assert caller.eligibility_calls == 0 and caller.extraction_calls == 2

    run_async(scenario())


@pytest.mark.parametrize("stage", ["eligibility", "extraction"])
def test_cancelled_stage_closes_run_and_is_recovered_only_by_its_stage(
    tmp_path: Path,
    stage: Stage,
) -> None:
    async def scenario() -> None:
        path = tmp_path / "cancelled.db"
        factory, _ = await _seed_audit(path)
        job_id = await _ready(factory, f"{URL}0")
        await _snapshot(factory, [f"{URL}0"])
        if stage == "eligibility":
            async with factory() as session:
                await jobs_repo.prepare_reprocess(
                    session, [job_id], from_phase="eligibility"
                )
            async with factory() as session, session.begin():
                row = await session.get(Job, job_id)
                assert row is not None
                row.reprocess_requested = False
        entered = asyncio.Event()

        class BlockingCaller(EligibleCaller):
            async def complete(
                self,
                messages: Sequence[Mapping[str, str]],
                schema: type[T],
            ) -> Completion[T]:
                entered.set()
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

        async with database(path) as second, httpx.AsyncClient() as client:
            track_engine(second.kw["bind"])
            task = asyncio.create_task(
                scheduler.run_pipeline(
                    [COMPANY],
                    stage=stage,
                    session_factory=second,
                    deps_factory=_deps(second, stage, BlockingCaller(), client),
                    policy=PipelinePolicy(model="test"),
                )
            )
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            async with factory() as session:
                job = await jobs_repo.get(session, job_id)
                rows = (
                    (
                        await session.execute(
                            select(PipelineRun).where(PipelineRun.id != "seed")
                        )
                    )
                    .scalars()
                    .all()
                )
            assert job is not None and job.status == "in_progress"
            assert len(rows) == 1 and rows[0].finished_at is not None
            async with factory() as session, session.begin():
                attempt = await session.get(JobAttempt, job.current_attempt_id)
                assert attempt is not None and attempt.finished_at is None
                attempt.started_at = utc_now() - timedelta(seconds=10)
            other: Stage = "eligibility" if stage == "extraction" else "extraction"
            result = await scheduler.run_pipeline(
                [COMPANY],
                stage=other,
                session_factory=second,
                deps_factory=_deps(second, other, EligibleCaller(), client),
                policy=PipelinePolicy(model="test", job_timeout=1),
            )
            assert result.reaped == 0 and not result.outcomes
            result = await scheduler.run_pipeline(
                [COMPANY],
                stage=stage,
                session_factory=second,
                deps_factory=_deps(second, stage, EligibleCaller(), client),
                policy=PipelinePolicy(model="test", job_timeout=1),
            )
            assert result.reaped == 1
            assert [outcome.status for outcome in result.outcomes] == [
                "ready_for_extraction"
                if stage == "eligibility"
                else "completed_eligible"
            ]

    run_async(scenario())
