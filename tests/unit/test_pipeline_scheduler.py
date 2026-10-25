"""Scheduler planning, reprocess, and aggregation tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TypeVar, cast
from urllib.parse import urlsplit

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import job_pipeline.cli.run as run_command
import job_pipeline.persistence.attempts_repo as attempts_repo
import job_pipeline.persistence.jobs_repo as jobs_repo
import job_pipeline.persistence.models as models
import job_pipeline.persistence.runs_repo as runs_repo
from job_discovery.domain.company import Company
from job_discovery.persistence import catalog_repo
from job_discovery.persistence import jobs_repo as discovery_jobs_repo
from job_discovery.persistence.engine import create_engine, create_session_factory
from job_discovery.settings import CaptureBrowser
from job_pipeline.llm import Completion
from job_pipeline.policy import PipelinePolicy
from job_pipeline.runner import scheduler
from job_pipeline.runner.observe import ObservationSummary
from job_pipeline.runner.scheduler import PipelineResult, browser_required, new_run_id
from job_pipeline.runner.worker import JobOutcome, TokenUsage, WorkerDeps
from tests.unit.asyncio_harness import run_async, track_engine

T = TypeVar("T", bound=BaseModel)

COMPANIES = (
    Company(
        name="Acme",
        job_board_url="https://acme.example/jobs",
        sample_job_url="https://acme.example/jobs/1",
    ),
    Company(
        name="Beta",
        job_board_url="https://beta.example/jobs",
        sample_job_url="https://beta.example/jobs/1",
    ),
)


class UnusedCaller:
    async def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        schema: type[T],
    ) -> Completion[T]:
        raise AssertionError("fake scheduler worker does not call the model")


async def _database(path: Path) -> async_sessionmaker[AsyncSession]:
    engine = track_engine(create_engine(path))
    async with engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)
    return cast(async_sessionmaker[AsyncSession], create_session_factory(engine))


async def _seed_snapshots(
    factory: async_sessionmaker[AsyncSession],
    urls: Mapping[Company, Sequence[str]],
) -> None:
    async with factory() as session:
        await catalog_repo.sync_catalog(session, list(urls))
    for company, company_urls in urls.items():
        async with factory() as session:
            await discovery_jobs_repo.replace_company_urls(
                session,
                company_slug=company.slug,
                urls=company_urls,
                captured_at=datetime(2026, 1, 1),
            )


def _deps_factory(
    factory: async_sessionmaker[AsyncSession],
    policy: PipelinePolicy,
) -> tuple[scheduler.DepsFactory, httpx.AsyncClient]:
    client = httpx.AsyncClient()

    def build(
        run_id: str,
        trigger: Literal["scheduled", "reprocess"],
        reason: str | None,
        http_semaphore: asyncio.Semaphore,
        browser_semaphore: asyncio.Semaphore,
        model_semaphore: asyncio.Semaphore,
        per_host_semaphores: dict[str, asyncio.Semaphore],
        per_host_lock: asyncio.Lock,
    ) -> WorkerDeps:
        return WorkerDeps(
            session_factory=factory,
            http_client=client,
            browser=None,
            caller=UnusedCaller(),
            policy=policy,
            run_id=run_id,
            trigger=trigger,
            reason=reason,
            companies={company.slug: company for company in COMPANIES},
            http_semaphore=http_semaphore,
            browser_semaphore=browser_semaphore,
            model_semaphore=model_semaphore,
            per_host_semaphores=per_host_semaphores,
            per_host_lock=per_host_lock,
        )

    return build, client


def test_run_ids_are_sortable_and_unique() -> None:
    identifiers = {new_run_id() for _ in range(50)}
    assert len(identifiers) == 50
    for identifier in identifiers:
        datetime.strptime(identifier[:15], "%Y%m%d_%H%M%S")
        assert len(identifier) == 24


def test_browser_required_for_dom_without_querying_attempts() -> None:
    company = Company(
        name="Acme",
        job_board_url="https://acme.example/jobs",
        sample_job_url="https://acme.example/jobs/1",
    )
    factory = cast(async_sessionmaker[AsyncSession], None)
    assert run_async(browser_required([company], [], factory))


def test_pipeline_result_counts_dispositions_and_tokens() -> None:
    outcomes = (
        JobOutcome(
            1,
            "acme",
            "key-1",
            "completed_eligible",
            "extraction",
            "eligible",
            None,
            TokenUsage(10, 5),
        ),
        JobOutcome(
            2,
            "acme",
            "key-2",
            "retry",
            "fetch",
            None,
            None,
            TokenUsage(),
        ),
        JobOutcome(
            3,
            "acme",
            "key-3",
            "skipped",
            "fetch",
            None,
            None,
            TokenUsage(),
        ),
    )
    result = PipelineResult(
        "run-1",
        ObservationSummary((), frozenset()),
        outcomes,
        reaped=2,
        dispositions={"completed_eligible": 3, "unknown": 1, "new": 2},
    )
    assert result.attempted == 2
    assert result.skipped == 1
    assert result.skipped_completed == 4
    assert result.counts["skipped_completed"] == 4
    assert result.eligible == 1
    assert result.retry == 1
    assert result.interrupted == 2
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 5
    assert result.tokens == 15


def test_run_pipeline_enforces_global_and_per_host_ceilings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> tuple[int, dict[str, int], int]:
        factory = await _database(tmp_path / "ceilings.db")
        await _seed_snapshots(
            factory,
            {
                COMPANIES[0]: [
                    "https://acme.example/jobs/1",
                    "https://acme.example/jobs/2",
                ],
                COMPANIES[1]: [
                    "https://beta.example/jobs/1",
                    "https://beta.example/jobs/2",
                ],
            },
        )
        active = 0
        high_water = 0
        active_by_host: dict[str, int] = {}
        high_water_by_host: dict[str, int] = {}
        target_reached = asyncio.Event()

        async def fake_process(job_id: int, *, deps: WorkerDeps) -> JobOutcome:
            nonlocal active, high_water
            async with deps.session_factory() as session:
                job = await jobs_repo.get(session, job_id)
                sources = await jobs_repo.list_sources(session, job_id)
            assert job is not None
            host = urlsplit(sources[0].url).hostname or ""
            async with deps.per_host_lock:
                host_semaphore = deps.per_host_semaphores.setdefault(
                    host, asyncio.Semaphore(deps.policy.per_host_concurrency)
                )
            async with deps.http_semaphore, host_semaphore:
                active += 1
                active_by_host[host] = active_by_host.get(host, 0) + 1
                high_water = max(high_water, active)
                high_water_by_host[host] = max(
                    high_water_by_host.get(host, 0), active_by_host[host]
                )
                if active == 3:
                    target_reached.set()
                await asyncio.wait_for(target_reached.wait(), timeout=1)
                await asyncio.sleep(0)
                active -= 1
                active_by_host[host] -= 1
            return JobOutcome(
                job.id,
                job.company_slug,
                job.url_key,
                "completed_eligible",
                "extraction",
                "eligible",
                None,
                TokenUsage(),
            )

        monkeypatch.setattr(scheduler, "process_job", fake_process)
        policy = PipelinePolicy(
            model="test",
            http_concurrency=3,
            per_host_concurrency=2,
        )
        deps_factory, client = _deps_factory(factory, policy)
        try:
            result = await scheduler.run_pipeline(
                COMPANIES,
                session_factory=factory,
                deps_factory=deps_factory,
                policy=policy,
            )
        finally:
            await client.aclose()
        return high_water, high_water_by_host, len(result.outcomes)

    high_water, high_water_by_host, outcome_count = run_async(scenario())
    assert high_water == 3
    assert max(high_water_by_host.values()) == 2
    assert all(count <= 2 for count in high_water_by_host.values())
    assert set(high_water_by_host) == {"acme.example", "beta.example"}
    assert outcome_count == 4


@pytest.mark.parametrize("semaphore_name", ["browser_semaphore", "model_semaphore"])
def test_run_pipeline_enforces_browser_and_model_ceilings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semaphore_name: str,
) -> None:
    async def scenario() -> tuple[int, int]:
        factory = await _database(tmp_path / f"{semaphore_name}.db")
        await _seed_snapshots(
            factory,
            {
                COMPANIES[0]: [
                    f"https://acme.example/jobs/{index}" for index in range(3)
                ]
            },
        )
        active = 0
        high_water = 0
        target_reached = asyncio.Event()

        async def fake_process(job_id: int, *, deps: WorkerDeps) -> JobOutcome:
            nonlocal active, high_water
            semaphore = cast(asyncio.Semaphore, getattr(deps, semaphore_name))
            async with semaphore:
                active += 1
                high_water = max(high_water, active)
                if active == 2:
                    target_reached.set()
                await asyncio.wait_for(target_reached.wait(), timeout=1)
                await asyncio.sleep(0)
                active -= 1
            async with deps.session_factory() as session:
                job = await jobs_repo.get(session, job_id)
            assert job is not None
            return JobOutcome(
                job.id,
                job.company_slug,
                job.url_key,
                "completed_eligible",
                "extraction",
                "eligible",
                None,
                TokenUsage(),
            )

        monkeypatch.setattr(scheduler, "process_job", fake_process)
        policy = PipelinePolicy(
            model="test", browser_concurrency=2, model_concurrency=2
        )
        deps_factory, client = _deps_factory(factory, policy)
        try:
            result = await scheduler.run_pipeline(
                [COMPANIES[0]],
                session_factory=factory,
                deps_factory=deps_factory,
                policy=policy,
            )
        finally:
            await client.aclose()
        return high_water, len(result.outcomes)

    assert run_async(scenario()) == (2, 3)


def test_run_pipeline_bounds_jobs_claimed_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> tuple[int, int, dict[str, int] | None]:
        factory = await _database(tmp_path / "in-flight.db")
        await _seed_snapshots(
            factory,
            {
                COMPANIES[0]: [
                    f"https://acme.example/jobs/{index}" for index in range(4)
                ]
            },
        )
        active = 0
        high_water = 0
        target_reached = asyncio.Event()

        async def fake_process(job_id: int, *, deps: WorkerDeps) -> JobOutcome:
            nonlocal active, high_water
            active += 1
            high_water = max(high_water, active)
            if active == 2:
                target_reached.set()
            await asyncio.wait_for(target_reached.wait(), timeout=1)
            await asyncio.sleep(0)
            active -= 1
            async with deps.session_factory() as session:
                job = await jobs_repo.get(session, job_id)
            assert job is not None
            return JobOutcome(
                job.id,
                job.company_slug,
                job.url_key,
                "completed_eligible",
                "extraction",
                "eligible",
                None,
                TokenUsage(),
            )

        monkeypatch.setattr(scheduler, "process_job", fake_process)
        policy = PipelinePolicy(model="test", http_concurrency=1, model_concurrency=1)
        assert policy.in_flight == 2
        deps_factory, client = _deps_factory(factory, policy)
        try:
            result = await scheduler.run_pipeline(
                [COMPANIES[0]],
                session_factory=factory,
                deps_factory=deps_factory,
                policy=policy,
            )
        finally:
            await client.aclose()
        return high_water, len(result.outcomes), dict(result.dispositions or {})

    high_water, outcome_count, dispositions = run_async(scenario())
    assert high_water == 2
    assert outcome_count == 4
    assert dispositions == {"new": 4}


def test_stale_bound_covers_every_timed_step() -> None:
    policy = PipelinePolicy(model="test", job_timeout=30)
    assert policy.stale_after.total_seconds() == 120


def test_run_pipeline_honors_limit_and_current_snapshot_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> tuple[list[int], int]:
        factory = await _database(tmp_path / "planning.db")
        await _seed_snapshots(
            factory,
            {COMPANIES[0]: ["https://acme.example/jobs/current"]},
        )
        async with factory() as session:
            await jobs_repo.observe(
                session,
                COMPANIES[0].slug,
                [("stale-key", "https://acme.example/jobs/stale")],
                datetime(2025, 1, 1),
            )
        selected: list[int] = []

        async def fake_process(job_id: int, *, deps: WorkerDeps) -> JobOutcome:
            selected.append(job_id)
            async with deps.session_factory() as session:
                job = await jobs_repo.get(session, job_id)
            assert job is not None
            return JobOutcome(
                job.id,
                job.company_slug,
                job.url_key,
                "skipped",
                "fetch",
                None,
                None,
                TokenUsage(),
            )

        monkeypatch.setattr(scheduler, "process_job", fake_process)
        policy = PipelinePolicy(model="test", limit=1)
        deps_factory, client = _deps_factory(factory, policy)
        try:
            result = await scheduler.run_pipeline(
                [COMPANIES[0]],
                session_factory=factory,
                deps_factory=deps_factory,
                policy=policy,
            )
        finally:
            await client.aclose()
        snapshot_keys = (
            result.observation.snapshot_keys if result.observation is not None else ()
        )
        return selected, len(snapshot_keys)

    assert run_async(scenario()) == ([2], 1)


def test_run_pipeline_reprocesses_exact_ids_outside_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> tuple[list[int], ObservationSummary | None]:
        factory = await _database(tmp_path / "reprocess.db")
        await _seed_snapshots(factory, {COMPANIES[0]: []})
        async with factory() as session:
            await jobs_repo.observe(
                session,
                COMPANIES[0].slug,
                [("old-key", "https://acme.example/jobs/old")],
                datetime(2025, 1, 1),
            )
        selected: list[int] = []

        async def fake_process(job_id: int, *, deps: WorkerDeps) -> JobOutcome:
            selected.append(job_id)
            async with deps.session_factory() as session:
                job = await jobs_repo.get(session, job_id)
            assert job is not None
            return JobOutcome(
                job.id,
                job.company_slug,
                job.url_key,
                "skipped",
                "fetch",
                None,
                None,
                TokenUsage(),
            )

        monkeypatch.setattr(scheduler, "process_job", fake_process)
        policy = PipelinePolicy(model="test", limit=0)
        deps_factory, client = _deps_factory(factory, policy)
        try:
            result = await scheduler.run_pipeline(
                [COMPANIES[0]],
                session_factory=factory,
                deps_factory=deps_factory,
                policy=policy,
                reprocess=[1, 1, 999],
                reason="audit",
            )
        finally:
            await client.aclose()
        return selected, result.observation

    assert run_async(scenario()) == ([1], None)


def test_run_pipeline_reaps_stale_attempt_before_planning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> tuple[int, str, str | None]:
        factory = await _database(tmp_path / "reap.db")
        await _seed_snapshots(factory, {COMPANIES[0]: []})
        async with factory() as session:
            await jobs_repo.observe(
                session,
                COMPANIES[0].slug,
                [("old-key", "https://acme.example/jobs/old")],
                datetime(2025, 1, 1),
            )
        async with factory() as session:
            await runs_repo.start_run(
                session,
                run_id="abandoned",
                selection=COMPANIES[0].slug,
                model="test",
                started_at=datetime(2025, 1, 1),
            )
        async with factory() as session:
            attempt_id = await attempts_repo.claim_and_start(
                session,
                job_id=1,
                run_id="abandoned",
                trigger="scheduled",
                reason=None,
                model="test",
                policy_version="policy",
                extractor_version="extractor",
                started_at=datetime(2025, 1, 1),
            )
        assert attempt_id is not None

        async def unexpected_process(job_id: int, *, deps: WorkerDeps) -> JobOutcome:
            raise AssertionError(f"unexpected planned job {job_id}")

        monkeypatch.setattr(scheduler, "process_job", unexpected_process)
        policy = PipelinePolicy(model="test", job_timeout=1)
        deps_factory, client = _deps_factory(factory, policy)
        try:
            result = await scheduler.run_pipeline(
                [COMPANIES[0]],
                session_factory=factory,
                deps_factory=deps_factory,
                policy=policy,
            )
        finally:
            await client.aclose()
        async with factory() as session:
            job = await jobs_repo.get(session, 1)
            attempt = await session.get(models.JobAttempt, attempt_id)
        assert job is not None
        assert attempt is not None
        return result.reaped, job.status, attempt.outcome

    assert run_async(scenario()) == (1, "retry", "interrupted")


@pytest.mark.parametrize(
    "failure", [None, RuntimeError("boom"), asyncio.CancelledError()]
)
def test_browser_factory_closes_on_every_exit(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException | None
) -> None:
    class Browser:
        def __init__(self) -> None:
            self.closed = 0

        async def close(self) -> None:
            self.closed += 1

    browser = Browser()

    @asynccontextmanager
    async def fake_playwright() -> AsyncIterator[object]:
        yield object()

    async def fake_launch(playwright: object) -> CaptureBrowser:
        return CaptureBrowser(cast(Any, browser), "test-agent")

    monkeypatch.setattr("playwright.async_api.async_playwright", fake_playwright)
    monkeypatch.setattr(run_command, "launch_capture_browser", fake_launch)

    async def scenario() -> None:
        async with run_command.browser_factory(True):
            if failure is not None:
                raise failure

    if failure is None:
        run_async(scenario())
    else:
        with pytest.raises(type(failure)):
            run_async(scenario())
    assert browser.closed == 1
