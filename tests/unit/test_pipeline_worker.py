"""Worker lifecycle tests against real pipeline persistence."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypeVar, cast

import httpx
import pytest
import respx
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import job_pipeline.persistence.attempts_repo as attempts_repo
import job_pipeline.persistence.decisions_repo as decisions_repo
import job_pipeline.persistence.jobs_repo as jobs_repo
import job_pipeline.persistence.models as models
import job_pipeline.persistence.results_repo as results_repo
import job_pipeline.persistence.runs_repo as runs_repo
from job_discovery.domain.company import Company
from job_discovery.persistence import models as discovery_models
from job_discovery.persistence.engine import create_engine, create_session_factory
from job_eligibility import EligibilityResponse
from job_extractor import ExtractionResponse
from job_pipeline.content import ladder
from job_pipeline.content.fetch import AddressResolver, FetchResult, fetch
from job_pipeline.content.model import AcquisitionFailure
from job_pipeline.llm import Completion, ModelCallError
from job_pipeline.policy import PipelinePolicy
from job_pipeline.runner.worker import WorkerDeps, process_job
from tests.unit.asyncio_harness import run_async, track_engine
from tests.unit.test_pipeline_cli import _seed_audit

T = TypeVar("T", bound=BaseModel)

COMPANY = Company(
    name="Acme",
    job_board_url="https://acme.example/jobs",
    sample_job_url="https://acme.example/jobs/1",
)


class IneligibleCaller:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        schema: type[T],
    ) -> Completion[T]:
        self.calls += 1
        assert schema is EligibilityResponse
        raw = """{
          "decision": "ineligible",
          "reason": "The role is based in Canada.",
          "evidence": ["Canada"],
          "signals": {
            "locations_named": ["Canada"],
            "locations_named_evidence": ["Canada"],
            "remote_scope": "none_stated",
            "remote_scope_evidence": [],
            "exhaustive_country_list": null,
            "exhaustive_country_list_evidence": [],
            "work_authorization_required_in": [],
            "work_authorization_evidence": []
          }
        }"""
        return Completion(
            parsed=schema.model_validate_json(raw),
            raw_json=raw,
            model="test",
            prompt_tokens=10,
            completion_tokens=2,
            latency_ms=1,
        )


POSTING_TEXT = (
    "Software Engineer in Costa Rica. Python is required. "
    + "Build reliable software systems in Costa Rica. " * 30
)
POSTING_HTML = (
    f"<html><main><h1>Software Engineer</h1><p>{POSTING_TEXT}</p></main></html>"
)


def _eligible_json() -> str:
    return json.dumps(
        {
            "decision": "eligible",
            "reason": "The role names Costa Rica.",
            "evidence": ["Costa Rica"],
            "signals": {
                "locations_named": ["Costa Rica"],
                "locations_named_evidence": ["Costa Rica"],
                "remote_scope": "none_stated",
                "remote_scope_evidence": [],
                "exhaustive_country_list": None,
                "exhaustive_country_list_evidence": [],
                "work_authorization_required_in": [],
                "work_authorization_evidence": [],
            },
        }
    )


def _extraction_json(*, grounded: bool = True) -> str:
    absent: dict[str, object] = {
        "state": "not_stated",
        "value": None,
        "evidence": [],
    }
    return json.dumps(
        {
            name: (
                {
                    "state": "extracted",
                    "value": "Software Engineer",
                    "evidence": [
                        "Software Engineer" if grounded else "Fabricated title"
                    ],
                }
                if name == "title"
                else absent
            )
            for name in ExtractionResponse.model_fields
        }
    )


class EligibleCaller:
    def __init__(self, *, fail_first_extraction: bool = False) -> None:
        self.fail_first_extraction = fail_first_extraction
        self.eligibility_calls = 0
        self.extraction_calls = 0

    async def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        schema: type[T],
    ) -> Completion[T]:
        if schema is EligibilityResponse:
            self.eligibility_calls += 1
            raw = _eligible_json()
        elif schema is ExtractionResponse:
            self.extraction_calls += 1
            raw = _extraction_json(
                grounded=not (self.fail_first_extraction and self.extraction_calls == 1)
            )
        else:
            raise AssertionError(schema)
        return Completion(
            parsed=schema.model_validate_json(raw),
            raw_json=raw,
            model="test",
            prompt_tokens=2,
            completion_tokens=3,
            latency_ms=1,
        )


class HangingCaller:
    async def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        schema: type[T],
    ) -> Completion[T]:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


async def _database(path: Path) -> async_sessionmaker[AsyncSession]:
    engine = track_engine(create_engine(path))
    async with engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)
    factory = create_session_factory(engine)
    async with factory() as session, session.begin():
        session.add(
            discovery_models.Company(
                slug=COMPANY.slug,
                name=COMPANY.name,
                job_board_url=COMPANY.job_board_url,
                strategy=COMPANY.strategy,
                synced_at=datetime(2026, 1, 1),
            )
        )
    async with factory() as session:
        await jobs_repo.observe(
            session,
            COMPANY.slug,
            [("key-1", "https://acme.example/jobs/1")],
            datetime(2026, 1, 1, tzinfo=UTC),
        )
    async with factory() as session:
        await runs_repo.start_run(
            session,
            run_id="run-1",
            stage="eligibility",
            trigger="scheduled",
            selection=COMPANY.slug,
            model="test",
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    return cast(async_sessionmaker[AsyncSession], factory)


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ladder, "fetch", _fetch_with_public_dns)


async def _fetch_with_public_dns(
    url: str,
    *,
    client: httpx.AsyncClient,
    job_board_url: str | None = None,
    resolver: AddressResolver | None = None,
) -> FetchResult | AcquisitionFailure:
    async def resolve(host: str) -> set[str]:
        return {"93.184.216.34"}

    return await fetch(
        url,
        client=client,
        job_board_url=job_board_url,
        resolver=resolve,
    )


def test_ineligible_stops_before_extraction_and_accounts_tokens(tmp_path: Path) -> None:
    async def scenario() -> tuple[str, str, int, int, int]:
        factory = await _database(tmp_path / "worker.db")
        caller = IneligibleCaller()
        html = (
            "<html><main><h1>Engineer</h1><p>" + "Canada " * 100 + "</p></main></html>"
        )
        with respx.mock:
            respx.get("https://acme.example/jobs/1").mock(
                return_value=httpx.Response(200, text=html)
            )
            async with httpx.AsyncClient() as client:
                outcome = await process_job(
                    1,
                    deps=WorkerDeps(
                        session_factory=factory,
                        stage="eligibility",
                        http_client=client,
                        browser=None,
                        caller=caller,
                        policy=PipelinePolicy(model="test"),
                        run_id="run-1",
                        trigger="scheduled",
                        reason=None,
                        companies={COMPANY.slug: COMPANY},
                    ),
                )
        async with factory() as session:
            job = await jobs_repo.get(session, 1)
            attempts = list(
                (await session.execute(select(models.JobAttempt))).scalars()
            )
        assert job is not None
        return (
            outcome.status,
            job.status,
            outcome.tokens.total,
            caller.calls,
            len(attempts),
        )

    assert run_async(scenario()) == (
        "completed_ineligible",
        "completed_ineligible",
        12,
        1,
        1,
    )


def test_hand_off_checkpoint_error_is_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def reject_hand_off(*args: object, **kwargs: object) -> int | None:
        raise decisions_repo.CheckpointError("eligible checkpoint disappeared")

    monkeypatch.setattr(decisions_repo, "hand_off_to_extraction", reject_hand_off)

    async def scenario() -> None:
        factory = await _database(tmp_path / "handoff-error.db")
        caller = EligibleCaller()
        with respx.mock:
            respx.get("https://acme.example/jobs/1").mock(
                return_value=httpx.Response(200, text=POSTING_HTML)
            )
            async with httpx.AsyncClient() as client:
                outcome = await process_job(
                    1,
                    deps=WorkerDeps(
                        session_factory=factory,
                        stage="eligibility",
                        http_client=client,
                        browser=None,
                        caller=caller,
                        policy=PipelinePolicy(model="test"),
                        run_id="run-1",
                        trigger="scheduled",
                        reason=None,
                        companies={COMPANY.slug: COMPANY},
                    ),
                )
        assert outcome.status == "failed"
        assert outcome.error is not None
        assert outcome.error.category == "missing_checkpoint"
        assert outcome.error.message == "eligible checkpoint disappeared"
        assert caller.extraction_calls == 0
        async with factory() as session:
            job = await jobs_repo.get(session, 1)
            attempt = (await session.execute(select(models.JobAttempt))).scalar_one()
        assert job is not None
        assert job.status == "failed"
        assert job.current_attempt_id is None
        assert job.next_attempt_after is None
        assert attempt.outcome == "failed"
        assert attempt.error_category == "missing_checkpoint"
        assert attempt.finished_at is not None

    run_async(scenario())


def test_cancellation_is_left_open_for_reaper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> tuple[str, str | None, datetime | None]:
        factory = await _database(tmp_path / "cancel.db")

        async def cancel(*args: object, **kwargs: object) -> object:
            raise asyncio.CancelledError

        monkeypatch.setattr("job_pipeline.runner.worker.acquire", cancel)
        async with httpx.AsyncClient() as client:
            deps = WorkerDeps(
                session_factory=factory,
                stage="eligibility",
                http_client=client,
                browser=None,
                caller=IneligibleCaller(),
                policy=PipelinePolicy(model="test"),
                run_id="run-1",
                trigger="scheduled",
                reason=None,
                companies={COMPANY.slug: COMPANY},
            )
            with pytest.raises(asyncio.CancelledError):
                await process_job(1, deps=deps)
        async with factory() as session:
            job = await jobs_repo.get(session, 1)
            attempt = (await session.execute(select(models.JobAttempt))).scalar_one()
        assert job is not None
        return job.status, attempt.outcome, attempt.finished_at

    assert run_async(scenario()) == ("in_progress", None, None)


def test_not_found_is_unavailable_and_lost_claim_is_skipped(tmp_path: Path) -> None:
    async def scenario() -> tuple[str, str]:
        factory = await _database(tmp_path / "not-found.db")
        caller = IneligibleCaller()
        deps: WorkerDeps
        with respx.mock:
            respx.get("https://acme.example/jobs/1").mock(
                return_value=httpx.Response(404)
            )
            async with httpx.AsyncClient() as client:
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
                    companies={COMPANY.slug: COMPANY},
                )
                first = await process_job(1, deps=deps)
                second = await process_job(1, deps=deps)
        return first.status, second.status

    assert run_async(scenario()) == ("unavailable", "skipped")


def test_eligible_extraction_success_persists_result(tmp_path: Path) -> None:
    async def scenario() -> tuple[str, str, str | None, bool, int, int, int]:
        factory = await _database(tmp_path / "eligible.db")
        caller = EligibleCaller()
        with respx.mock(assert_all_called=True) as router:
            router.get("https://acme.example/jobs/1").mock(
                return_value=httpx.Response(200, text=POSTING_HTML)
            )
            async with httpx.AsyncClient() as client:
                outcome = await process_job(
                    1,
                    deps=WorkerDeps(
                        session_factory=factory,
                        stage="eligibility",
                        http_client=client,
                        browser=None,
                        caller=caller,
                        policy=PipelinePolicy(model="test"),
                        run_id="run-1",
                        trigger="scheduled",
                        reason=None,
                        companies={COMPANY.slug: COMPANY},
                    ),
                )
        async with factory() as session:
            job = await jobs_repo.get(session, 1)
            decision = await decisions_repo.current(session, 1)
            result = await results_repo.current(session, 1)
        assert job is not None
        return (
            outcome.status,
            job.status,
            decision.decision if decision is not None else None,
            result is not None,
            outcome.tokens.total,
            caller.eligibility_calls,
            caller.extraction_calls,
        )

    assert run_async(scenario()) == (
        "ready_for_extraction",
        "ready_for_extraction",
        "eligible",
        False,
        5,
        1,
        0,
    )


def test_extraction_failure_retries_from_checkpoint_without_eligibility_call(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[list[str], str, int, int, int, int]:
        factory = await _database(tmp_path / "resume.db")
        caller = EligibleCaller(fail_first_extraction=True)
        policy = PipelinePolicy(model="test", retry_backoff=timedelta(0))
        with respx.mock(assert_all_called=True) as router:
            router.get("https://acme.example/jobs/1").mock(
                return_value=httpx.Response(200, text=POSTING_HTML)
            )
            async with httpx.AsyncClient() as client:
                deps = WorkerDeps(
                    session_factory=factory,
                    stage="eligibility",
                    http_client=client,
                    browser=None,
                    caller=caller,
                    policy=policy,
                    run_id="run-1",
                    trigger="scheduled",
                    reason=None,
                    companies={COMPANY.slug: COMPANY},
                )
                ready = await process_job(1, deps=deps)
                assert ready.status == "ready_for_extraction"
                deps = replace(deps, stage="extraction", http_client=None)
                first = await process_job(1, deps=deps)
                second = await process_job(1, deps=deps)
        async with factory() as session:
            job = await jobs_repo.get(session, 1)
            decisions = await decisions_repo.history(session, 1)
            results = await results_repo.history(session, 1)
            attempts = await session.execute(
                select(models.JobAttempt).order_by(models.JobAttempt.id)
            )
        assert job is not None
        return (
            [first.status, second.status],
            job.next_phase,
            caller.eligibility_calls,
            caller.extraction_calls,
            len(decisions),
            len(results) + len(attempts.scalars().all()),
        )

    assert run_async(scenario()) == (
        ["retry", "completed_eligible"],
        "extraction",
        1,
        2,
        1,
        4,
    )


def test_timeout_retries_then_exhausts_budget(tmp_path: Path) -> None:
    async def scenario() -> tuple[list[str], list[str], str, int]:
        factory = await _database(tmp_path / "timeout.db")
        policy = PipelinePolicy(
            model="test",
            job_timeout=0.01,
            retry_limit=2,
            retry_backoff=timedelta(0),
        )
        with respx.mock(assert_all_called=True) as router:
            router.get("https://acme.example/jobs/1").mock(
                return_value=httpx.Response(200, text=POSTING_HTML)
            )
            async with httpx.AsyncClient() as client:
                deps = WorkerDeps(
                    session_factory=factory,
                    stage="eligibility",
                    http_client=client,
                    browser=None,
                    caller=HangingCaller(),
                    policy=policy,
                    run_id="run-1",
                    trigger="scheduled",
                    reason=None,
                    companies={COMPANY.slug: COMPANY},
                )
                first = await process_job(1, deps=deps)
                second = await process_job(1, deps=deps)
        async with factory() as session:
            job = await jobs_repo.get(session, 1)
        assert job is not None
        return (
            [first.status, second.status],
            [first.error.category, second.error.category]
            if first.error is not None and second.error is not None
            else [],
            job.status,
            job.attempt_count,
        )

    assert run_async(scenario()) == (
        ["retry", "failed"],
        ["timeout", "timeout"],
        "failed",
        2,
    )


def test_queue_wait_is_not_charged_against_the_job_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two jobs sharing one HTTP slot both finish when each step fits the budget.

    The second job waits for the first's fetch before it can start its own,
    so its wall time exceeds the timeout. Only the fetch it actually runs is
    timed, so neither job may be recorded as a timeout.
    """

    async def scenario() -> tuple[list[str], list[str | None], float]:
        factory = await _database(tmp_path / "queue.db")
        async with factory() as session:
            await jobs_repo.observe(
                session,
                COMPANY.slug,
                [("key-2", "https://acme.example/jobs/2")],
                datetime(2026, 1, 1, tzinfo=UTC),
            )
        real_acquire = ladder.acquire
        step_seconds = 0.3

        async def slow_acquire(*args: object, **kwargs: object) -> object:
            await asyncio.sleep(step_seconds)
            return await real_acquire(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr("job_pipeline.runner.worker.acquire", slow_acquire)
        policy = PipelinePolicy(model="test", job_timeout=step_seconds + 0.3)
        first_html = (
            "<html><main><h1>Engineer</h1><p>" + "Canada " * 100 + "</p></main></html>"
        )
        second_html = (
            "<html><main><h1>Analyst</h1><p>"
            + "Canada Toronto " * 100
            + "</p></main></html>"
        )
        with respx.mock(assert_all_called=True) as router:
            router.get("https://acme.example/jobs/1").mock(
                return_value=httpx.Response(200, text=first_html)
            )
            router.get("https://acme.example/jobs/2").mock(
                return_value=httpx.Response(200, text=second_html)
            )
            async with httpx.AsyncClient() as client:
                deps = WorkerDeps(
                    session_factory=factory,
                    stage="eligibility",
                    http_client=client,
                    browser=None,
                    caller=IneligibleCaller(),
                    policy=policy,
                    run_id="run-1",
                    trigger="scheduled",
                    reason=None,
                    companies={COMPANY.slug: COMPANY},
                    http_semaphore=asyncio.Semaphore(1),
                )
                started = asyncio.get_running_loop().time()
                outcomes = await asyncio.gather(
                    process_job(1, deps=deps), process_job(2, deps=deps)
                )
                elapsed = asyncio.get_running_loop().time() - started
        return (
            [outcome.status for outcome in outcomes],
            [
                outcome.error.category if outcome.error is not None else None
                for outcome in outcomes
            ],
            elapsed,
        )

    statuses, categories, elapsed = run_async(scenario())
    assert statuses == ["completed_ineligible", "completed_ineligible"]
    assert categories == [None, None]
    # The second fetch could only start after the first released the slot,
    # so the run as a whole outlived one job's timeout budget.
    assert elapsed > 0.6


@pytest.mark.parametrize(
    "missing", ["content", "decision", "ineligible", "old_content"]
)
def test_missing_extraction_checkpoint_is_terminal_without_model_calls(
    tmp_path: Path,
    missing: str,
) -> None:
    async def scenario() -> None:
        factory, job_id = await _seed_audit(tmp_path / "missing.db")
        async with factory() as session, session.begin():
            job = await session.get(models.Job, job_id)
            assert job is not None
            job.status = "ready_for_extraction"
            job.next_phase = "extraction"
            if missing == "content":
                job.current_content_id = None
            if missing in {"decision", "ineligible"}:
                await session.execute(
                    update(models.JobDecision).values(
                        is_current=missing != "decision", decision="ineligible"
                    )
                )
            if missing == "old_content":
                old = await session.get(models.JobContent, job.current_content_id)
                assert old is not None
                new = models.JobContent(
                    job_id=job_id,
                    content_hash="new",
                    fingerprint="new",
                    resolved_url=old.resolved_url,
                    http_status=200,
                    method="main_content",
                    text=old.text,
                    retrieved_at=datetime(2026, 1, 2),
                )
                session.add(new)
                await session.flush()
                job.current_content_id = new.id
        caller = EligibleCaller()
        from tests.unit.test_pipeline_cli import COMPANY as AUDIT_COMPANY

        outcome = await process_job(
            job_id,
            deps=WorkerDeps(
                session_factory=factory,
                stage="extraction",
                http_client=None,
                browser=None,
                caller=caller,
                policy=PipelinePolicy(model="test"),
                run_id="seed",
                trigger="scheduled",
                reason=None,
                companies={AUDIT_COMPANY.slug: AUDIT_COMPANY},
            ),
        )
        assert outcome.status == "failed"
        assert (
            outcome.error is not None and outcome.error.category == "missing_checkpoint"
        )
        assert caller.eligibility_calls == caller.extraction_calls == 0
        async with factory() as session:
            attempts = await attempts_repo.history(session, job_id)
            current_job = await jobs_repo.get(session, job_id)
        assert current_job is not None and current_job.attempt_count == 1
        assert attempts[-1].error_category == "missing_checkpoint"
        assert attempts[-1].finished_at is not None

    run_async(scenario())


@pytest.mark.parametrize("internal", [False, True])
def test_retry_budgets_and_backoff_restart_at_stage_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    internal: bool,
) -> None:
    async def scenario() -> None:
        factory = await _database(tmp_path / "budget.db")
        moment = datetime(2026, 10, 1)
        monkeypatch.setattr("job_pipeline.policy.utc_now", lambda: moment)
        monkeypatch.setattr("job_pipeline.runner.worker.utc_now", lambda: moment)

        class FailingCaller(EligibleCaller):
            async def complete(
                self,
                messages: Sequence[Mapping[str, str]],
                schema: type[T],
            ) -> Completion[T]:
                if schema is ExtractionResponse:
                    self.extraction_calls += 1
                    if internal:
                        raise RuntimeError("internal extraction failure")
                    raise ModelCallError("test", "retryable extraction failure")
                return await super().complete(messages, schema)

        caller = FailingCaller()
        fetch_calls = 0
        from job_pipeline.content.model import AcquiredContent

        async def acquire(
            *args: object, **kwargs: object
        ) -> AcquiredContent | AcquisitionFailure:
            nonlocal fetch_calls
            fetch_calls += 1
            if fetch_calls <= 2:
                return AcquisitionFailure("transport", "retryable fetch failure")
            return AcquiredContent(
                POSTING_TEXT,
                "Software Engineer",
                COMPANY.sample_job_url,
                200,
                "main_content",
                None,
                None,
                moment,
                "hash",
            )

        monkeypatch.setattr("job_pipeline.runner.worker.acquire", acquire)
        async with httpx.AsyncClient() as client:
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
                companies={COMPANY.slug: COMPANY},
            )
            for number in (1, 2):
                outcome = await process_job(1, deps=deps)
                assert outcome.status == "retry"
                async with factory() as session:
                    job = await jobs_repo.get(session, 1)
                assert job is not None and job.next_attempt_after == moment + timedelta(
                    days=number
                )
                moment += timedelta(days=number)
            outcome = await process_job(1, deps=deps)
            assert outcome.status == "ready_for_extraction"
            assert caller.extraction_calls == 0
            async with factory() as session:
                job = await jobs_repo.get(session, 1)
                attempts = await attempts_repo.history(session, 1)
            assert (
                job is not None
                and job.attempt_count == 0
                and job.current_attempt_id is None
            )
            assert attempts[-1].outcome == "ready_for_extraction"
            assert attempts[-1].finished_at is not None
            deps = replace(deps, stage="extraction", http_client=None)
            budget = 2 if internal else 3
            for number in range(1, budget + 1):
                outcome = await process_job(1, deps=deps)
                assert outcome.status == ("failed" if number == budget else "retry")
                async with factory() as session:
                    job = await jobs_repo.get(session, 1)
                assert job is not None and job.attempt_count == number
                if number != budget:
                    assert job.next_attempt_after == moment + timedelta(days=number)
                    moment += timedelta(days=number)
            assert fetch_calls == 3
            assert caller.eligibility_calls == 1
            assert caller.extraction_calls == budget

    run_async(scenario())


@pytest.mark.parametrize("stage", ["eligibility", "extraction"])
def test_internal_failure_cap_is_two_in_each_stage(
    tmp_path: Path, stage: models.Stage
) -> None:
    async def scenario() -> None:
        factory, job_id = await _seed_audit(tmp_path / "internal.db")
        async with factory() as session:
            await jobs_repo.prepare_reprocess(session, [job_id], from_phase=stage)

        class InternalCaller(EligibleCaller):
            async def complete(
                self, messages: Sequence[Mapping[str, str]], schema: type[T]
            ) -> Completion[T]:
                raise RuntimeError("internal")

        from tests.unit.test_pipeline_cli import COMPANY as AUDIT_COMPANY

        async with httpx.AsyncClient() as client:
            deps = WorkerDeps(
                session_factory=factory,
                stage=stage,
                http_client=client if stage == "eligibility" else None,
                browser=None,
                caller=InternalCaller(),
                policy=PipelinePolicy(model="test", retry_backoff=timedelta(0)),
                run_id="seed",
                trigger="reprocess",
                reason="test",
                companies={AUDIT_COMPANY.slug: AUDIT_COMPANY},
            )
            first = await process_job(job_id, deps=deps)
            second = await process_job(job_id, deps=replace(deps, trigger="scheduled"))
        assert [first.status, second.status] == ["retry", "failed"]

    run_async(scenario())
