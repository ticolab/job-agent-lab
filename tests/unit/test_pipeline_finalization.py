from __future__ import annotations

import asyncio
import json
import sqlite3
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from typing import TypeVar, cast

import httpx
import pytest
import respx
from pydantic import BaseModel
from sqlalchemy import event, or_, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

from job_discovery.persistence.engine import create_engine, create_session_factory
from job_discovery.persistence.timestamps import utc_now
from job_eligibility import POLICY_VERSION, EligibilityResponse
from job_extractor import EXTRACTOR_VERSION
from job_pipeline.content import ladder
from job_pipeline.content.model import AcquiredContent
from job_pipeline.llm import Completion, StructuredCaller
from job_pipeline.persistence import (
    attempts_repo,
    contents_repo,
    decisions_repo,
    finalize,
    jobs_repo,
    models,
    results_repo,
    runs_repo,
)
from job_pipeline.policy import PipelinePolicy
from job_pipeline.runner import scheduler
from job_pipeline.runner.worker import JobOutcome, WorkerDeps, process_job
from tests.unit.asyncio_harness import run_async, track_engine
from tests.unit.test_pipeline_cli import COMPANY as STAGE_COMPANY
from tests.unit.test_pipeline_cli import _database as _stage_database
from tests.unit.test_pipeline_stages import _deps, _ready, _snapshot
from tests.unit.test_pipeline_worker import (
    COMPANY,
    POSTING_HTML,
    EligibleCaller,
    IneligibleCaller,
    _database,
    _fetch_with_public_dns,
)

T = TypeVar("T", bound=BaseModel)
Factory = async_sessionmaker[AsyncSession]
CANADA_HTML = (
    "<html><main><h1>Engineer</h1><p>" + "Canada " * 100 + "</p></main></html>"
)
PATHS = (
    "P1-ineligible",
    "P1-unknown",
    "P2-reused",
    "P3-extraction",
    "P4-retry-after-model",
    "P4-unavailable",
    "P4-unknown-company",
    "P5-merge",
)


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ladder, "fetch", _fetch_with_public_dns)


class UnknownCaller(IneligibleCaller):
    async def complete(
        self, messages: Sequence[Mapping[str, str]], schema: type[T]
    ) -> Completion[T]:
        completion = await super().complete(messages, schema)
        payload = json.loads(completion.raw_json)
        payload["decision"] = "eligible"
        payload["reason"] = "Unsupported eligibility claim."
        payload["evidence"] = ["Canada"]
        payload["signals"]["locations_named"] = []
        payload["signals"]["locations_named_evidence"] = []
        raw = json.dumps(payload)
        return replace(completion, parsed=schema.model_validate_json(raw), raw_json=raw)


class NoModelCaller:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, messages: Sequence[Mapping[str, str]], schema: type[T]
    ) -> Completion[T]:
        self.calls += 1
        pytest.fail("this path must not call the model")


@dataclass(frozen=True)
class PathCase:
    name: str
    job_id: int
    deps: WorkerDeps
    repository: ModuleType
    operation: str
    status: str
    phase: str
    tokens: tuple[int | None, int | None] = (None, None)
    decision: str | None = None
    error_category: str | None = None
    reused_decision_id: int | None = None
    canonical: jobs_repo.JobRecord | None = None


async def _orphans(factory: Factory) -> list[tuple[int, int]]:
    async with factory() as session:
        rows = await session.execute(
            select(models.JobAttempt.id, models.JobAttempt.job_id)
            .join(models.Job, models.Job.id == models.JobAttempt.job_id)
            .where(
                models.JobAttempt.finished_at.is_(None),
                or_(
                    models.Job.status != "in_progress",
                    models.Job.current_attempt_id.is_(None),
                    models.Job.current_attempt_id != models.JobAttempt.id,
                ),
            )
            .order_by(models.JobAttempt.id)
        )
        return [(row.id, row.job_id) for row in rows]


async def _seed_reused(factory: Factory) -> int:
    async with factory() as session:
        attempt_id = await attempts_repo.start(
            session,
            job_id=1,
            run_id="run-1",
            trigger="scheduled",
            reason=None,
            model="test",
            policy_version=POLICY_VERSION,
            extractor_version=EXTRACTOR_VERSION,
            started_at=utc_now(),
        )
    async with factory() as session:
        content_id = await contents_repo.store(
            session,
            1,
            contents_repo.AcquiredContent(
                content_hash="reused-content",
                fingerprint="reused-fingerprint",
                requisition_id=None,
                resolved_url=COMPANY.sample_job_url,
                http_status=200,
                method="main_content",
                title="Engineer",
                text="Canada " * 100,
                jsonld_json=None,
                retrieved_at=utc_now(),
            ),
        )
    async with factory() as session:
        decision_id = await decisions_repo.accept(
            session,
            job_id=1,
            attempt_id=attempt_id,
            content_id=content_id,
            decision=decisions_repo.DecisionInput(
                "ineligible", "Canada", '["Canada"]', POLICY_VERSION
            ),
            decided_at=utc_now(),
        )
    assert decision_id is not None
    async with factory() as session:
        assert await attempts_repo.finish(
            session,
            attempt_id,
            phase_reached="eligibility",
            outcome="retry",
            finished_at=utc_now(),
        )
    async with factory() as session, session.begin():
        await session.execute(
            update(models.Job)
            .where(models.Job.id == 1)
            .values(
                status="retry",
                next_phase="eligibility",
                next_attempt_after=utc_now() - timedelta(days=1),
                current_content_id=content_id,
                attempt_count=1,
            )
        )
    return decision_id


async def _build_path(
    name: str, factory: Factory, client: httpx.AsyncClient, router: respx.MockRouter
) -> PathCase:
    caller: StructuredCaller = NoModelCaller()
    html = CANADA_HTML
    if name == "P1-ineligible":
        caller = IneligibleCaller()
    elif name == "P1-unknown":
        caller = UnknownCaller()
    elif name in {"P3-extraction", "P4-retry-after-model", "P5-merge"}:
        caller = EligibleCaller(fail_first_extraction=name == "P4-retry-after-model")
        html = POSTING_HTML
    router.get(COMPANY.sample_job_url).mock(
        return_value=httpx.Response(404 if name == "P4-unavailable" else 200, text=html)
    )
    deps = WorkerDeps(
        session_factory=factory,
        stage="eligibility",
        http_client=client,
        browser=None,
        caller=caller,
        policy=PipelinePolicy(model="test"),
        run_id="run-1",
        trigger="scheduled",
        reason=None,
        companies={} if name == "P4-unknown-company" else {COMPANY.slug: COMPANY},
    )
    if name.startswith("P1-"):
        unknown = name == "P1-unknown"
        case = PathCase(
            name,
            1,
            deps,
            decisions_repo,
            "accept_and_complete",
            "unknown" if unknown else "completed_ineligible",
            "eligibility",
            tokens=(10, 2),
            decision="unknown" if unknown else "ineligible",
        )
    elif name == "P2-reused":
        decision_id = await _seed_reused(factory)
        case = PathCase(
            name,
            1,
            deps,
            jobs_repo,
            "release",
            "completed_ineligible",
            "eligibility",
            decision="ineligible",
            reused_decision_id=decision_id,
        )
    elif name in {"P3-extraction", "P4-retry-after-model"}:
        ready = await process_job(1, deps=deps)
        assert ready.status == "ready_for_extraction"
        async with factory() as session:
            await runs_repo.start_run(
                session,
                run_id="run-extraction",
                stage="extraction",
                trigger="scheduled",
                selection=COMPANY.slug,
                model="test",
                started_at=utc_now(),
            )
        deps = replace(
            deps, stage="extraction", http_client=None, run_id="run-extraction"
        )
        retry = name == "P4-retry-after-model"
        case = PathCase(
            name,
            1,
            deps,
            jobs_repo if retry else results_repo,
            "release" if retry else "accept_and_complete",
            "retry" if retry else "completed_eligible",
            "extraction",
            tokens=(2, 3),
            decision="eligible",
            error_category="ungrounded_evidence" if retry else None,
        )
    elif name in {"P4-unavailable", "P4-unknown-company"}:
        unavailable = name == "P4-unavailable"
        case = PathCase(
            name,
            1,
            deps,
            jobs_repo,
            "release",
            "unavailable" if unavailable else "retry",
            "fetch",
            error_category="not_found" if unavailable else "internal",
        )
    elif name == "P5-merge":
        ready = await process_job(1, deps=deps)
        assert ready.status == "ready_for_extraction"
        async with factory() as session:
            canonical = await jobs_repo.get(session, 1)
        assert canonical is not None
        async with factory() as session:
            await jobs_repo.observe(
                session,
                COMPANY.slug,
                [("key-2", "https://acme.example/jobs/2")],
                utc_now(),
            )
        router.get("https://acme.example/jobs/2").mock(
            return_value=httpx.Response(200, text=POSTING_HTML)
        )
        case = PathCase(
            name,
            2,
            replace(deps, caller=NoModelCaller()),
            jobs_repo,
            "merge_duplicate",
            "merged",
            "fetch",
            canonical=canonical,
        )
    else:
        raise AssertionError(name)
    assert await _orphans(factory) == []
    return case


async def _committed_state(
    case: PathCase,
) -> tuple[jobs_repo.JobRecord, attempts_repo.AttemptRecord]:
    factory = case.deps.session_factory
    async with factory() as session:
        job = await jobs_repo.get(session, case.job_id)
        attempts = await attempts_repo.history(session, case.job_id)
        decision = await decisions_repo.current(session, case.job_id)
        result = await results_repo.current(session, case.job_id)
        content = await contents_repo.current(session, case.job_id)
        sources = await jobs_repo.list_sources(session, case.job_id)
    assert job is not None
    assert job.status == case.status
    assert job.next_phase == case.phase
    assert job.current_attempt_id is None
    assert attempts
    attempt = attempts[-1]
    assert attempt.run_id == case.deps.run_id
    if case.status == "retry":
        assert job.next_attempt_after is not None
        assert job.next_attempt_after > utc_now()
    else:
        assert job.next_attempt_after is None
    if case.decision is not None:
        assert content is not None
        assert decision is not None
        assert decision.decision == case.decision
        assert decision.content_id == content.id
        if case.name.startswith("P1-"):
            assert decision.attempt_id == attempt.id
        if case.reused_decision_id is not None:
            assert decision.id == case.reused_decision_id
            assert decision.attempt_id != attempt.id
            async with factory() as session:
                assert len(await decisions_repo.history(session, case.job_id)) == 1
    else:
        assert decision is None
    if case.status == "completed_eligible":
        assert result is not None
        assert result.attempt_id == attempt.id
        assert content is not None and result.content_id == content.id
    else:
        assert result is None
    if case.canonical is not None:
        assert job.merged_into_id == case.canonical.id
        assert content is not None
        assert sources == ()
        async with factory() as session:
            canonical = await jobs_repo.get(session, case.canonical.id)
            canonical_sources = await jobs_repo.list_sources(session, case.canonical.id)
            canonical_attempts = await attempts_repo.history(session, case.canonical.id)
        assert canonical == case.canonical
        assert {source.url for source in canonical_sources} == {
            COMPANY.sample_job_url,
            "https://acme.example/jobs/2",
        }
        assert len(canonical_attempts) == 1
        assert canonical_attempts[0].outcome == "ready_for_extraction"
        assert canonical_attempts[0].finished_at is not None
    return job, attempt


async def _assert_finalized(
    case: PathCase, job: jobs_repo.JobRecord, attempt: attempts_repo.AttemptRecord
) -> None:
    orphans = await _orphans(case.deps.session_factory)
    assert orphans == [], (
        f"{case.name}: committed {job.status}, lease={job.current_attempt_id}, "
        f"unfinished orphan attempts={orphans}"
    )
    assert attempt.finished_at is not None
    assert attempt.outcome == job.status
    assert attempt.phase_reached == case.phase
    assert (attempt.prompt_tokens, attempt.completion_tokens) == case.tokens
    assert attempt.error_category == case.error_category
    if case.error_category is None:
        assert attempt.error_message is None
    else:
        assert attempt.error_message
        if case.name == "P4-unknown-company":
            assert attempt.error_message == f"unknown company: {COMPANY.slug}"


@pytest.mark.parametrize("path", PATHS)
def test_path_builder_commits_expected_state(tmp_path: Path, path: str) -> None:
    async def scenario() -> None:
        factory = await _database(tmp_path / "baseline.db")
        with respx.mock(assert_all_called=False) as router:
            async with httpx.AsyncClient() as client:
                case = await _build_path(path, factory, client, router)
                outcome = await process_job(case.job_id, deps=case.deps)
                job, attempt = await _committed_state(case)
                assert outcome.status == job.status
                await _assert_finalized(case, job, attempt)

    run_async(scenario())


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("mode", ["after_commit", "restart_after_commit"])
def test_finalization_after_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str, mode: str
) -> None:
    async def scenario() -> None:
        factory = await _database(tmp_path / "after-commit.db")
        with respx.mock(assert_all_called=False) as router:
            async with httpx.AsyncClient() as client:
                case = await _build_path(path, factory, client, router)
                real_call = cast(
                    Callable[..., Awaitable[object]],
                    getattr(case.repository, case.operation),
                )
                committed_calls = 0

                async def cancel_after_commit(
                    *args: object, **kwargs: object
                ) -> object:
                    nonlocal committed_calls
                    result = await real_call(*args, **kwargs)
                    assert result is not None and result is not False
                    committed_calls += 1
                    raise asyncio.CancelledError

                with monkeypatch.context() as patch:
                    patch.setattr(case.repository, case.operation, cancel_after_commit)
                    with pytest.raises(asyncio.CancelledError):
                        await process_job(case.job_id, deps=case.deps)
                assert committed_calls == 1
                job, attempt = await _committed_state(case)
                if mode == "restart_after_commit":
                    async with factory() as session:
                        before = await attempts_repo.history(session, case.job_id)
                    caller = NoModelCaller()
                    restarted = await process_job(
                        case.job_id, deps=replace(case.deps, caller=caller)
                    )
                    assert restarted.status == "skipped"
                    assert caller.calls == 0
                    async with factory() as session:
                        after = await attempts_repo.history(session, case.job_id)
                        restarted_job = await jobs_repo.get(session, case.job_id)
                    assert after == before
                    assert restarted_job == job
                    assert restarted_job.attempt_count == job.attempt_count
                await _assert_finalized(case, job, attempt)

    run_async(scenario())


@pytest.mark.parametrize("path", PATHS)
def test_finalization_finish_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    async def scenario() -> None:
        factory = await _database(tmp_path / "finish-raises.db")
        with respx.mock(assert_all_called=False) as router:
            async with httpx.AsyncClient() as client:
                case = await _build_path(path, factory, client, router)
                finish_calls = 0

                async def locked_finish(*args: object, **kwargs: object) -> bool:
                    nonlocal finish_calls
                    finish_calls += 1
                    raise OperationalError(
                        "UPDATE job_attempts",
                        {},
                        sqlite3.OperationalError("database is locked"),
                    )

                outcome: JobOutcome | None = None
                raised: OperationalError | None = None
                with monkeypatch.context() as patch:
                    patch.setattr(attempts_repo, "finish", locked_finish)
                    try:
                        outcome = await process_job(case.job_id, deps=case.deps)
                    except OperationalError as exc:
                        raised = exc
                job, attempt = await _committed_state(case)
                orphans = await _orphans(factory)
                returned = outcome.status if outcome is not None else None
                assert returned == job.status, (
                    f"{case.name}: committed={job.status}, returned={returned}, "
                    f"raised={raised!r}, finish_calls={finish_calls}, "
                    f"attempt.outcome={attempt.outcome}, orphans={orphans}"
                )
                assert raised is None
                await _assert_finalized(case, job, attempt)

    run_async(scenario())


async def _assert_run_counts(
    factory: Factory, result: scheduler.PipelineResult
) -> None:
    async with factory() as session:
        run = await session.get(models.PipelineRun, result.run_id)
        attempts = tuple(
            (
                await session.scalars(
                    select(models.JobAttempt).where(
                        models.JobAttempt.run_id == result.run_id
                    )
                )
            ).all()
        )
        for attempt in attempts:
            job = await jobs_repo.get(session, attempt.job_id)
            assert job is not None and job.status == attempt.outcome
            assert attempt.finished_at is not None
            current_results = tuple(
                (
                    await session.scalars(
                        select(models.JobResult).where(
                            models.JobResult.job_id == attempt.job_id,
                            models.JobResult.is_current.is_(True),
                        )
                    )
                ).all()
            )
            assert len(current_results) <= 1
    assert run is not None and run.finished_at is not None
    counts = json.loads(run.counts_json or "{}")
    assert counts == result.counts
    tally = Counter(attempt.outcome for attempt in attempts)
    mapping = {
        "retry": "retry",
        "failed": "failed",
        **(
            {
                "ready_for_extraction": "ready_for_extraction",
                "ineligible": "completed_ineligible",
                "unknown": "unknown",
                "unavailable": "unavailable",
                "merged": "merged",
            }
            if result.stage == "eligibility"
            else {"extracted": "completed_eligible"}
        ),
    }
    for key, status in mapping.items():
        assert counts[key] == tally[status]
    assert counts["attempted"] == len(attempts)
    assert counts["skipped"] == 0
    assert counts["prompt_tokens"] == sum(row.prompt_tokens or 0 for row in attempts)
    assert counts["completion_tokens"] == sum(
        row.completion_tokens or 0 for row in attempts
    )
    assert {outcome.job_id: outcome.status for outcome in result.outcomes} == {
        row.job_id: row.outcome for row in attempts
    }
    assert await _orphans(factory) == []


def test_concurrent_mixed_stages_commit_matching_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        path = tmp_path / "mixed.db"
        factory = await _stage_database(path)
        async with factory() as session:
            await runs_repo.start_run(
                session,
                run_id="seed",
                stage="eligibility",
                trigger="scheduled",
                selection=STAGE_COMPANY.slug,
                model="test",
                started_at=utc_now(),
            )
        base = STAGE_COMPANY.sample_job_url
        ready_c = await _ready(factory, f"{base}0")
        ready_d = await _ready(factory, f"{base}1")
        async with factory() as session, session.begin():
            await session.execute(
                update(models.JobContent)
                .where(models.JobContent.job_id == ready_d)
                .values(text="Software Engineer in Costa Rica. Retry target D.")
            )
        fresh_a, fresh_b = f"{base}2", f"{base}3"
        await _snapshot(factory, [f"{base}0", f"{base}1", fresh_a, fresh_b])
        entered: set[models.Stage] = set()
        both = asyncio.Event()
        failed_d = 0

        class MixedCaller(EligibleCaller):
            async def complete(
                self, messages: Sequence[Mapping[str, str]], schema: type[T]
            ) -> Completion[T]:
                nonlocal failed_d
                stage: models.Stage = (
                    "eligibility" if schema is EligibilityResponse else "extraction"
                )
                entered.add(stage)
                if len(entered) == 2:
                    both.set()
                await asyncio.wait_for(both.wait(), 5)
                assert await _orphans(factory) == []
                text = " ".join(message["content"] for message in messages)
                if "Retry target D" in text:
                    failed_d += 1
                    return await EligibleCaller(fail_first_extraction=True).complete(
                        messages, schema
                    )
                if "Fresh target B" in text:
                    return await IneligibleCaller().complete(messages, schema)
                return await super().complete(messages, schema)

        async def acquire(url: str, **kwargs: object) -> AcquiredContent:
            assert url in {fresh_a, fresh_b}
            text = (
                "Software Engineer in Costa Rica. Fresh target A."
                if url == fresh_a
                else "Software Engineer in Canada. Fresh target B."
            )
            return AcquiredContent(
                text,
                "Software Engineer",
                url,
                200,
                "main_content",
                None,
                None,
                utc_now(),
                f"hash-{url}",
            )

        monkeypatch.setattr("job_pipeline.runner.worker.acquire", acquire)
        engine = track_engine(create_engine(path))
        second = create_session_factory(engine)
        assert factory.kw["bind"] is not second.kw["bind"]
        caller = MixedCaller()
        async with httpx.AsyncClient() as client:
            results = await asyncio.wait_for(
                asyncio.gather(
                    scheduler.run_pipeline(
                        [STAGE_COMPANY],
                        stage="eligibility",
                        session_factory=factory,
                        deps_factory=_deps(factory, "eligibility", caller, client),
                        policy=PipelinePolicy(model="test"),
                    ),
                    scheduler.run_pipeline(
                        [STAGE_COMPANY],
                        stage="extraction",
                        session_factory=second,
                        deps_factory=_deps(second, "extraction", caller),
                        policy=PipelinePolicy(model="test"),
                    ),
                ),
                15,
            )
        assert entered == {"eligibility", "extraction"}
        assert failed_d == 1
        assert sorted(
            outcome.status for result in results for outcome in result.outcomes
        ) == [
            "completed_eligible",
            "completed_ineligible",
            "ready_for_extraction",
            "retry",
        ]
        assert {outcome.job_id for outcome in results[1].outcomes} == {ready_c, ready_d}
        for result in results:
            await _assert_run_counts(factory, result)

    run_async(scenario())


def test_concurrent_duplicates_finish_merge_without_touching_canonical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        factory = await _stage_database(tmp_path / "duplicates.db")
        first_url = STAGE_COMPANY.sample_job_url
        second_url = f"{first_url}0"
        await _snapshot(factory, [first_url, second_url])
        async with factory() as session:
            await jobs_repo.observe(
                session,
                STAGE_COMPANY.slug,
                [(first_url, first_url), (second_url, second_url)],
                utc_now(),
            )
        entered = asyncio.Event()
        merged = asyncio.Event()
        canonical_before: jobs_repo.JobRecord | None = None
        attempt_before: attempts_repo.AttemptRecord | None = None
        checkpoints = 0
        caller_calls = 0

        class CanonicalCaller(EligibleCaller):
            async def complete(
                self, messages: Sequence[Mapping[str, str]], schema: type[T]
            ) -> Completion[T]:
                nonlocal canonical_before, attempt_before, caller_calls
                caller_calls += 1
                async with factory() as session:
                    canonical_before = await jobs_repo.get(session, 1)
                    attempts = await attempts_repo.history(session, 1)
                attempt_before = attempts[-1]
                assert canonical_before is not None
                assert canonical_before.current_attempt_id == attempt_before.id
                assert canonical_before.status == "in_progress"
                assert attempt_before.finished_at is None
                entered.set()
                await asyncio.wait_for(merged.wait(), 5)
                return await super().complete(messages, schema)

        async def acquire(url: str, **kwargs: object) -> AcquiredContent:
            if url == second_url:
                await asyncio.wait_for(entered.wait(), 5)
            return AcquiredContent(
                "Software Engineer in Costa Rica. Identical duplicate content.",
                "Software Engineer",
                url,
                200,
                "main_content",
                None,
                None,
                utc_now(),
                "identical-hash",
            )

        real_merge = jobs_repo.merge_duplicate
        real_finish = attempts_repo.finish

        async def inspect_merge(*args: object, **kwargs: object) -> object:
            nonlocal checkpoints
            call = cast(Callable[..., Awaitable[int | None]], real_merge)
            result = await call(*args, **kwargs)
            if kwargs["job_id"] == 2:
                assert result == 1
                async with factory() as session:
                    duplicate = await jobs_repo.get(session, 2)
                    attempts = await attempts_repo.history(session, 2)
                    canonical = await jobs_repo.get(session, 1)
                    canonical_attempts = await attempts_repo.history(session, 1)
                    sources = await jobs_repo.list_sources(session, 1)
                assert duplicate is not None and duplicate.status == "merged"
                assert duplicate.merged_into_id == 1
                assert duplicate.current_attempt_id is None
                assert attempts[-1].outcome == "merged"
                assert attempts[-1].phase_reached == "fetch"
                assert attempts[-1].finished_at is not None
                assert canonical == canonical_before
                assert canonical_attempts == (attempt_before,)
                assert {source.url for source in sources} == {first_url, second_url}
                assert await _orphans(factory) == []
                checkpoints += 1
                merged.set()
            return result

        async def forbid_duplicate_finish(
            session: AsyncSession, attempt_id: int, **kwargs: object
        ) -> bool:
            async with factory() as inspection:
                row = await inspection.get(models.JobAttempt, attempt_id)
                assert row is not None
                assert row.job_id != 2, "merged attempt needs no separate finish"
            call = cast(Callable[..., Awaitable[bool]], real_finish)
            return await call(session, attempt_id, **kwargs)

        monkeypatch.setattr("job_pipeline.runner.worker.acquire", acquire)
        monkeypatch.setattr(jobs_repo, "merge_duplicate", inspect_merge)
        monkeypatch.setattr(attempts_repo, "finish", forbid_duplicate_finish)
        caller = CanonicalCaller()
        async with httpx.AsyncClient() as client:
            result = await asyncio.wait_for(
                scheduler.run_pipeline(
                    [STAGE_COMPANY],
                    stage="eligibility",
                    session_factory=factory,
                    deps_factory=_deps(factory, "eligibility", caller, client),
                    policy=PipelinePolicy(model="test"),
                ),
                15,
            )
        assert checkpoints == 1 and caller_calls == 1
        assert [outcome.status for outcome in result.outcomes] == [
            "ready_for_extraction",
            "merged",
        ]
        await _assert_run_counts(factory, result)

    run_async(scenario())


@dataclass(frozen=True)
class Checkpoints:
    decisions: tuple[decisions_repo.DecisionRecord, ...]
    results: tuple[results_repo.ResultRecord, ...]
    sources: tuple[jobs_repo.JobSourceRecord, ...]


async def _checkpoints(case: PathCase) -> Checkpoints:
    async with case.deps.session_factory() as session:
        return Checkpoints(
            await decisions_repo.history(session, case.job_id),
            await results_repo.history(session, case.job_id),
            await jobs_repo.list_sources(session, case.job_id),
        )


async def _assert_rollback(case: PathCase, before: Checkpoints) -> int:
    factory = case.deps.session_factory
    async with factory() as session:
        job = await jobs_repo.get(session, case.job_id)
        attempts = await attempts_repo.history(session, case.job_id)
        content = await contents_repo.current(session, case.job_id)
    assert job is not None
    attempt = attempts[-1]
    assert job.status == "in_progress", f"rollback lost: status={job.status}"
    assert job.current_attempt_id == attempt.id
    assert job.next_phase == case.phase
    assert attempt.finished_at is None
    assert attempt.outcome is None
    assert attempt.prompt_tokens is None and attempt.completion_tokens is None
    assert await _checkpoints(case) == before
    assert all(row.attempt_id != attempt.id for row in before.decisions)
    assert all(row.attempt_id != attempt.id for row in before.results)
    if case.canonical is not None:
        assert job.merged_into_id is None
        assert content is not None
        assert {source.url for source in before.sources} == {
            "https://acme.example/jobs/2"
        }
        async with factory() as session:
            assert await jobs_repo.get(session, case.canonical.id) == case.canonical
            sources = await jobs_repo.list_sources(session, case.canonical.id)
        assert {source.url for source in sources} == {COMPANY.sample_job_url}
    assert await _orphans(factory) == []
    return attempt.id


async def _reap_owned(case: PathCase, attempt_id: int) -> None:
    factory = case.deps.session_factory
    ended = utc_now()
    other_stage: models.Stage = (
        "extraction" if case.deps.stage == "eligibility" else "eligibility"
    )
    async with factory() as session:
        assert (
            await runs_repo.reap_stale(
                session,
                stage=other_stage,
                older_than=ended + timedelta(seconds=1),
                finished_at=ended,
            )
            == 0
        )
    async with factory() as session:
        assert (
            await runs_repo.reap_stale(
                session,
                stage=case.deps.stage,
                older_than=ended + timedelta(seconds=1),
                finished_at=ended,
            )
            == 1
        )
    async with factory() as session:
        job = await jobs_repo.get(session, case.job_id)
        attempts = await attempts_repo.history(session, case.job_id)
    assert job is not None
    assert job.status == "retry" and job.next_phase == case.phase
    assert job.current_attempt_id is None
    assert job.next_attempt_after == ended
    assert attempts[-1].id == attempt_id
    assert attempts[-1].outcome == "interrupted"
    assert attempts[-1].error_category == "interrupted"
    assert attempts[-1].finished_at == ended
    assert await _orphans(factory) == []


async def _before_commit_scenario(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    factory = await _database(tmp_path / "before-commit.db")
    with respx.mock(assert_all_called=False) as router:
        async with httpx.AsyncClient() as client:
            case = await _build_path(path, factory, client, router)
            before = await _checkpoints(case)
            calls = 0

            async def cancel(*args: object, **kwargs: object) -> None:
                nonlocal calls
                calls += 1
                raise asyncio.CancelledError

            with monkeypatch.context() as patch:
                patch.setattr(finalize, "finish_owned_attempt", cancel)
                with pytest.raises(asyncio.CancelledError):
                    await process_job(case.job_id, deps=case.deps)
            assert calls == 1
            attempt_id = await _assert_rollback(case, before)
            await _reap_owned(case, attempt_id)


@pytest.mark.parametrize("path", PATHS)
def test_finalization_before_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    run_async(_before_commit_scenario(tmp_path, monkeypatch, path))


def test_before_commit_detects_non_atomic_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_finish = finalize.finish_owned_attempt

    real_release = jobs_repo.release

    async def non_atomic_release(*args: object, **kwargs: object) -> object:
        injected = cast(Callable[..., Awaitable[None]], finalize.finish_owned_attempt)

        async def separated(session: AsyncSession, **fields: object) -> None:
            await session.commit()
            await injected(session, **fields)

        with monkeypatch.context() as patch:
            patch.setattr(finalize, "finish_owned_attempt", separated)
            release = cast(Callable[..., Awaitable[bool]], real_release)
            return await release(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(jobs_repo, "release", non_atomic_release)
        with pytest.raises(AssertionError, match="status"):
            run_async(_before_commit_scenario(tmp_path, monkeypatch, "P4-unavailable"))
    assert finalize.finish_owned_attempt is real_finish
    assert jobs_repo.release is real_release


def _locked() -> OperationalError:
    return OperationalError(
        "UPDATE job_attempts", {}, sqlite3.OperationalError("database is locked")
    )


@pytest.mark.parametrize(
    "path", ["P1-ineligible", "P1-unknown", "P2-reused", "P3-extraction", "P5-merge"]
)
def test_finalization_rollback_then_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    async def scenario() -> None:
        factory = await _database(tmp_path / "recorded.db")
        with respx.mock(assert_all_called=False) as router:
            async with httpx.AsyncClient() as client:
                case = await _build_path(path, factory, client, router)
                before = await _checkpoints(case)
                if path == "P2-reused":
                    async with factory() as session, session.begin():
                        await session.execute(
                            update(models.Job)
                            .where(models.Job.id == case.job_id)
                            .values(attempt_count=0)
                        )
                real_finish = cast(
                    Callable[..., Awaitable[None]], finalize.finish_owned_attempt
                )
                calls = 0

                async def fail_once(session: AsyncSession, **kwargs: object) -> None:
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        raise _locked()
                    await real_finish(session, **kwargs)

                with monkeypatch.context() as patch:
                    patch.setattr(finalize, "finish_owned_attempt", fail_once)
                    outcome = await process_job(case.job_id, deps=case.deps)
                assert calls == 2
                assert outcome.status == "retry"
                assert (
                    outcome.error is not None and outcome.error.category == "internal"
                )
                assert await _checkpoints(case) == before
                async with factory() as session:
                    job = await jobs_repo.get(session, case.job_id)
                    attempts = await attempts_repo.history(session, case.job_id)
                assert job is not None and job.status == "retry"
                assert job.next_phase == case.phase
                assert job.current_attempt_id is None
                assert job.next_attempt_after is not None
                assert job.next_attempt_after > utc_now()
                assert job.merged_into_id is None
                await _assert_finalized(
                    replace(case, error_category="internal"), job, attempts[-1]
                )

    run_async(scenario())


@pytest.mark.parametrize("path", PATHS)
def test_finalization_unrecordable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    path: str,
) -> None:
    async def scenario() -> None:
        factory = await _database(tmp_path / "unrecordable.db")
        with respx.mock(assert_all_called=False) as router:
            async with httpx.AsyncClient() as client:
                case = await _build_path(path, factory, client, router)
                before = await _checkpoints(case)
                calls = 0

                async def locked(*args: object, **kwargs: object) -> None:
                    nonlocal calls
                    calls += 1
                    raise _locked()

                caplog.clear()
                with monkeypatch.context() as patch:
                    patch.setattr(finalize, "finish_owned_attempt", locked)
                    if path == "P4-unknown-company":
                        with pytest.raises(
                            OperationalError, match="database is locked"
                        ):
                            await process_job(case.job_id, deps=case.deps)
                    else:
                        outcome = await process_job(case.job_id, deps=case.deps)
                        assert outcome.status == "failed"
                        assert outcome.error is not None
                        assert outcome.error.category == "internal"
                        assert "the reaper will recover it" in caplog.text
                assert calls >= 1
                attempt_id = await _assert_rollback(case, before)
                await _reap_owned(case, attempt_id)

    run_async(scenario())


@pytest.mark.parametrize("path", ["P1-ineligible", "P3-extraction"])
@pytest.mark.parametrize("reclaimed", [False, True], ids=["retry", "reclaimed"])
def test_stale_caller_cannot_finalize(
    tmp_path: Path, path: str, reclaimed: bool
) -> None:
    async def scenario() -> None:
        factory = await _database(tmp_path / "stale.db")
        with respx.mock(assert_all_called=False) as router:
            async with httpx.AsyncClient() as client:
                case = await _build_path(path, factory, client, router)
                before = await _checkpoints(case)
                stale_id: int | None = None
                replacement_id: int | None = None
                expected_job: jobs_repo.JobRecord | None = None
                expected_attempts: tuple[attempts_repo.AttemptRecord, ...] = ()

                class ReapingCaller:
                    async def complete(
                        self, messages: Sequence[Mapping[str, str]], schema: type[T]
                    ) -> Completion[T]:
                        nonlocal \
                            stale_id, \
                            replacement_id, \
                            expected_job, \
                            expected_attempts
                        async with factory() as session:
                            attempts = await attempts_repo.history(session, case.job_id)
                        stale_id = attempts[-1].id
                        await _reap_owned(case, stale_id)
                        if reclaimed:
                            async with factory() as session:
                                replacement_id = await attempts_repo.claim_and_start(
                                    session,
                                    job_id=case.job_id,
                                    run_id=case.deps.run_id,
                                    trigger="scheduled",
                                    reason=None,
                                    model="test",
                                    policy_version=POLICY_VERSION,
                                    extractor_version=EXTRACTOR_VERSION,
                                    started_at=utc_now(),
                                    stage=case.deps.stage,
                                )
                            assert replacement_id is not None
                        async with factory() as session:
                            expected_job = await jobs_repo.get(session, case.job_id)
                            expected_attempts = await attempts_repo.history(
                                session, case.job_id
                            )
                        return await case.deps.caller.complete(messages, schema)

                outcome = await process_job(
                    case.job_id, deps=replace(case.deps, caller=ReapingCaller())
                )
                assert outcome.status == "skipped"
                assert await _checkpoints(case) == before
                async with factory() as session:
                    job = await jobs_repo.get(session, case.job_id)
                    attempts = await attempts_repo.history(session, case.job_id)
                assert job == expected_job
                assert attempts == expected_attempts
                stale = next(row for row in attempts if row.id == stale_id)
                assert stale.outcome == "interrupted" and stale.finished_at is not None
                if reclaimed:
                    assert job is not None and job.status == "in_progress"
                    assert job.current_attempt_id == replacement_id
                    assert attempts[-1].finished_at is None
                assert await _orphans(factory) == []

    run_async(scenario())


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("persistent", [False, True], ids=["once", "persistent"])
def test_finalization_commit_boundary_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    path: str,
    persistent: bool,
) -> None:
    async def scenario() -> None:
        factory = await _database(tmp_path / "commit-boundary.db")
        with respx.mock(assert_all_called=False) as router:
            async with httpx.AsyncClient() as client:
                case = await _build_path(path, factory, client, router)
                if path == "P2-reused":
                    async with factory() as session, session.begin():
                        await session.execute(
                            update(models.Job)
                            .where(models.Job.id == case.job_id)
                            .values(attempt_count=0)
                        )
                before = await _checkpoints(case)
                real_finish = cast(
                    Callable[..., Awaitable[None]], finalize.finish_owned_attempt
                )
                armed: set[Session] = set()
                helper_calls = 0
                commits = 0
                failures = 0
                rolled_back = 0

                async def arm(session: AsyncSession, **kwargs: object) -> None:
                    nonlocal helper_calls
                    if helper_calls == 1:
                        assert failures == rolled_back == 1
                        await _assert_rollback(case, before)
                    await real_finish(session, **kwargs)
                    helper_calls += 1
                    assert session.in_transaction()
                    armed.add(session.sync_session)

                def before_commit(session: Session) -> None:
                    nonlocal commits, failures
                    if session not in armed:
                        return
                    armed.remove(session)
                    commits += 1
                    job = session.get(models.Job, case.job_id)
                    assert job is not None and job.current_attempt_id is None
                    attempt = session.scalar(
                        select(models.JobAttempt)
                        .where(models.JobAttempt.job_id == case.job_id)
                        .order_by(models.JobAttempt.id.desc())
                        .limit(1)
                    )
                    assert attempt is not None and attempt.finished_at is not None
                    assert attempt.outcome == job.status
                    if persistent or failures == 0:
                        failures += 1
                        raise _locked()

                def after_rollback(session: Session) -> None:
                    nonlocal rolled_back
                    if commits > rolled_back:
                        rolled_back += 1

                event.listen(Session, "before_commit", before_commit)
                event.listen(Session, "after_rollback", after_rollback)
                caplog.clear()
                outcome: JobOutcome | None = None
                try:
                    with monkeypatch.context() as patch:
                        patch.setattr(finalize, "finish_owned_attempt", arm)
                        if path == "P4-unknown-company":
                            with pytest.raises(
                                OperationalError, match="database is locked"
                            ):
                                await process_job(case.job_id, deps=case.deps)
                        else:
                            outcome = await process_job(case.job_id, deps=case.deps)
                finally:
                    event.remove(Session, "before_commit", before_commit)
                    event.remove(Session, "after_rollback", after_rollback)
                assert helper_calls == commits
                assert not armed
                assert failures == (commits if persistent else 1)
                assert rolled_back == failures
                assert await _checkpoints(case) == before
                cannot_record = persistent or path in {
                    "P4-retry-after-model",
                    "P4-unknown-company",
                }
                if cannot_record:
                    if path != "P4-unknown-company":
                        assert outcome is not None and outcome.status == "failed"
                        assert outcome.error is not None
                        assert outcome.error.category == "internal"
                        assert "the reaper will recover it" in caplog.text
                    attempt_id = await _assert_rollback(case, before)
                    await _reap_owned(case, attempt_id)
                else:
                    assert commits == 2
                    assert outcome is not None and outcome.status == "retry"
                    assert outcome.error is not None
                    assert outcome.error.category == "internal"
                    async with factory() as session:
                        job = await jobs_repo.get(session, case.job_id)
                        attempts = await attempts_repo.history(session, case.job_id)
                    assert job is not None and job.status == "retry"
                    assert job.current_attempt_id is None
                    assert job.next_phase == case.phase
                    assert job.merged_into_id is None
                    assert job.next_attempt_after is not None
                    assert job.next_attempt_after > utc_now()
                    await _assert_finalized(
                        replace(case, error_category="internal"), job, attempts[-1]
                    )
                assert await _orphans(factory) == []

    run_async(scenario())


@pytest.mark.parametrize(
    "path",
    [
        "P1-unknown",
        "P2-reused",
        "P4-retry-after-model",
        "P4-unavailable",
        "P4-unknown-company",
        "P5-merge",
    ],
)
@pytest.mark.parametrize("reclaimed", [False, True], ids=["retry", "reclaimed"])
def test_stale_repository_boundary_reports_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str, reclaimed: bool
) -> None:
    async def scenario() -> None:
        factory = await _database(tmp_path / "stale-boundary.db")
        with respx.mock(assert_all_called=False) as router:
            async with httpx.AsyncClient() as client:
                case = await _build_path(path, factory, client, router)
                real_call = cast(
                    Callable[..., Awaitable[object]],
                    getattr(case.repository, case.operation),
                )
                calls = 0
                expected_job: jobs_repo.JobRecord | None = None
                expected_attempts: tuple[attempts_repo.AttemptRecord, ...] = ()
                expected_checkpoints: Checkpoints | None = None
                canonical_before = case.canonical
                replacement_id: int | None = None

                async def reap_then_call(*args: object, **kwargs: object) -> object:
                    nonlocal calls, expected_job, expected_attempts
                    nonlocal expected_checkpoints, replacement_id
                    calls += 1
                    assert calls == 1
                    async with factory() as session:
                        attempts = await attempts_repo.history(session, case.job_id)
                    await _reap_owned(case, attempts[-1].id)
                    if reclaimed:
                        async with factory() as session:
                            replacement_id = await attempts_repo.claim_and_start(
                                session,
                                job_id=case.job_id,
                                run_id=case.deps.run_id,
                                trigger="scheduled",
                                reason=None,
                                model="test",
                                policy_version=POLICY_VERSION,
                                extractor_version=EXTRACTOR_VERSION,
                                started_at=utc_now(),
                                stage=case.deps.stage,
                            )
                        assert replacement_id is not None
                    async with factory() as session:
                        expected_job = await jobs_repo.get(session, case.job_id)
                        expected_attempts = await attempts_repo.history(
                            session, case.job_id
                        )
                    expected_checkpoints = await _checkpoints(case)
                    return await real_call(*args, **kwargs)

                with monkeypatch.context() as patch:
                    patch.setattr(case.repository, case.operation, reap_then_call)
                    outcome = await process_job(case.job_id, deps=case.deps)
                assert calls == 1
                assert outcome.status == "skipped"
                assert await _checkpoints(case) == expected_checkpoints
                async with factory() as session:
                    job = await jobs_repo.get(session, case.job_id)
                    attempts = await attempts_repo.history(session, case.job_id)
                    if canonical_before is not None:
                        assert (
                            await jobs_repo.get(session, canonical_before.id)
                            == canonical_before
                        )
                assert job == expected_job
                assert attempts == expected_attempts
                assert job is not None
                if reclaimed:
                    assert job.status == "in_progress"
                    assert job.current_attempt_id == replacement_id
                    assert attempts[-1].finished_at is None
                    stale = attempts[-2]
                else:
                    assert job.status == "retry" and job.current_attempt_id is None
                    stale = attempts[-1]
                assert stale.outcome == "interrupted"
                assert stale.finished_at is not None
                assert expected_checkpoints is not None
                assert all(
                    row.attempt_id != stale.id for row in expected_checkpoints.decisions
                )
                assert all(
                    row.attempt_id != stale.id for row in expected_checkpoints.results
                )
                assert await _orphans(factory) == []

    run_async(scenario())


@pytest.mark.parametrize(
    "path", ["P1-unknown", "P4-unavailable", "P4-failed", "P2-reused"]
)
def test_scheduler_counts_additional_dispositions(tmp_path: Path, path: str) -> None:
    async def scenario() -> None:
        factory = await _database(tmp_path / "additional-counts.db")
        with respx.mock(assert_all_called=False) as router:
            async with httpx.AsyncClient() as client:
                builder = "P4-unknown-company" if path == "P4-failed" else path
                case = await _build_path(builder, factory, client, router)
                if path == "P4-failed":
                    async with factory() as session, session.begin():
                        await session.execute(
                            update(models.Job)
                            .where(models.Job.id == case.job_id)
                            .values(attempt_count=1)
                        )
                    case = replace(case, status="failed")

                def deps_factory(
                    run_id: str,
                    stage: models.Stage,
                    trigger: str,
                    reason: str | None,
                    http_semaphore: asyncio.Semaphore,
                    browser_semaphore: asyncio.Semaphore,
                    model_semaphore: asyncio.Semaphore,
                    per_host_semaphores: dict[str, asyncio.Semaphore],
                    per_host_lock: asyncio.Lock,
                ) -> WorkerDeps:
                    return replace(
                        case.deps,
                        run_id=run_id,
                        stage=stage,
                        reason=reason,
                        http_semaphore=http_semaphore,
                        browser_semaphore=browser_semaphore,
                        model_semaphore=model_semaphore,
                        per_host_semaphores=per_host_semaphores,
                        per_host_lock=per_host_lock,
                    )

                result = await scheduler.run_pipeline(
                    [COMPANY],
                    stage=case.deps.stage,
                    session_factory=factory,
                    deps_factory=deps_factory,
                    policy=case.deps.policy,
                    reprocess=[case.job_id],
                    reason="count acceptance coverage",
                )
                assert len(result.outcomes) == 1
                assert result.outcomes[0].status == case.status
                case = replace(case, deps=replace(case.deps, run_id=result.run_id))
                job, attempt = await _committed_state(case)
                await _assert_finalized(case, job, attempt)
                await _assert_run_counts(factory, result)

    run_async(scenario())
