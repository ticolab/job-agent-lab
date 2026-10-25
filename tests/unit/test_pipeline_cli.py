from __future__ import annotations

import _thread
import argparse
import asyncio
import json
import threading
import traceback
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

import httpx
import pytest
import respx
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from job_discovery.domain.company import Company
from job_discovery.persistence import jobs_repo as discovery_jobs_repo
from job_discovery.persistence import models as discovery_models
from job_discovery.persistence.engine import create_engine, create_session_factory
from job_eligibility import EligibilityResponse
from job_extractor import ExtractionResponse
from job_pipeline.cli import common, reprocess, run
from job_pipeline.cli import main as main_module
from job_pipeline.cli.main import build_parser, main
from job_pipeline.llm import Completion, StructuredCaller
from job_pipeline.persistence import (
    attempts_repo,
    contents_repo,
    decisions_repo,
    jobs_repo,
    models,
    results_repo,
    runs_repo,
)
from tests.unit.asyncio_harness import run_async, track_engine

COMPANY = Company(
    name="Acme",
    aliases=("acme",),
    job_board_url="https://job-boards.greenhouse.io/acme",
    sample_job_url="https://job-boards.greenhouse.io/acme/jobs/1",
    strategy="greenhouse",
)
OTHER_COMPANY = Company(
    name="Beta",
    aliases=("beta",),
    job_board_url="https://job-boards.greenhouse.io/beta",
    sample_job_url="https://job-boards.greenhouse.io/beta/jobs/1",
    strategy="greenhouse",
)
URL = "https://job-boards.greenhouse.io/acme/jobs/1"
T = TypeVar("T", bound=BaseModel)


class AcceptedCaller:
    async def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        schema: type[T],
    ) -> Completion[T]:
        if schema is EligibilityResponse:
            raw = json.dumps(
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
        elif schema is ExtractionResponse:
            not_stated: dict[str, object] = {
                "state": "not_stated",
                "value": None,
                "evidence": [],
            }
            raw = json.dumps(
                {
                    name: (
                        {
                            "state": "extracted",
                            "value": "software engineer",
                            "evidence": ["software engineer"],
                        }
                        if name == "title"
                        else not_stated
                    )
                    for name in ExtractionResponse.model_fields
                }
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


def _args(command: str, *argv: str) -> argparse.Namespace:
    return build_parser().parse_args([command, *argv])


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
    return factory


async def _seed_snapshot(path: Path) -> async_sessionmaker[AsyncSession]:
    factory = await _database(path)
    async with factory() as session:
        await discovery_jobs_repo.replace_company_urls(
            session,
            company_slug=COMPANY.slug,
            urls=[URL],
            captured_at=datetime(2026, 1, 1),
        )
    return factory


@pytest.fixture
def catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    companies = [COMPANY, OTHER_COMPANY]

    def find(handle: str) -> Company | None:
        return next(
            (
                company
                for company in companies
                if handle.casefold() in {company.slug, *company.aliases}
            ),
            None,
        )

    monkeypatch.setattr(common, "COMPANIES", companies)
    monkeypatch.setattr(reprocess, "COMPANIES", companies)
    monkeypatch.setattr(reprocess, "find_company", find)
    monkeypatch.setattr(common, "find_company", find)
    monkeypatch.setattr("job_discovery.catalog.find_company", find)


def test_dispatcher_and_policy_flags_use_real_parser() -> None:
    parser = build_parser()
    assert {
        row.name
        for row in __import__(
            "job_pipeline.cli.main", fromlist=["SUBCOMMANDS"]
        ).SUBCOMMANDS
    } == {"preview", "run", "reprocess", "show"}
    args = parser.parse_args(
        [
            "run",
            "--all",
            "--model",
            "test-model",
            "--limit",
            "3",
            "--http-concurrency",
            "4",
            "--browser-concurrency",
            "2",
            "--model-concurrency",
            "5",
            "--timeout-seconds",
            "30",
        ]
    )
    policy = common.build_policy(args)
    assert policy.model == "test-model"
    assert policy.limit == 3
    assert policy.http_concurrency == 4
    assert policy.browser_concurrency == 2
    assert policy.model_concurrency == 5
    assert policy.job_timeout == 30
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["preview", "--all", "--limit", "0"])
    assert exc.value.code == 2


def test_selection_errors_match_scan(
    catalog: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert common.select_companies(_args("preview", "-c", "acme")) == [COMPANY]
    with pytest.raises(SystemExit) as exc:
        common.select_companies(_args("preview", "-c", "missing"))
    assert exc.value.code == 2
    assert "Unknown company handle(s): missing" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("model", "credential"),
    [
        ("gpt-4.1-mini", ("OpenAI", "OPENAI_API_KEY")),
        ("openai/gpt-4.1-mini", ("OpenAI", "OPENAI_API_KEY")),
        ("anthropic/claude-sonnet-4", ("Anthropic", "ANTHROPIC_API_KEY")),
        ("claude-sonnet-4", ("Anthropic", "ANTHROPIC_API_KEY")),
        ("test-model", None),
    ],
)
def test_provider_credential_is_conservative(
    model: str, credential: tuple[str, str] | None
) -> None:
    assert common.provider_credential(model) == credential


def test_run_async_cancels_the_task_on_keyboard_interrupt() -> None:
    cancelled = threading.Event()

    async def hang() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    # Deliver the interrupt to the main thread while it waits on the worker,
    # exactly as a terminal Ctrl-C would.
    timer = threading.Timer(0.3, _thread.interrupt_main)
    timer.start()
    try:
        with pytest.raises(KeyboardInterrupt) as info:
            common.run_async(hang())
    finally:
        timer.cancel()
    assert cancelled.is_set(), "".join(traceback.format_tb(info.tb))


def test_main_turns_keyboard_interrupt_into_exit_130(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def interrupted(_args: argparse.Namespace) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "job_pipeline.cli.main._BY_NAME",
        {"show": replace(main_module._BY_NAME["show"], execute=interrupted)},
    )
    with pytest.raises(SystemExit) as exc:
        main(["show", "--job", "1"])
    assert exc.value.code == 130
    assert "reaped by the next run" in capsys.readouterr().err


def test_preview_observes_and_never_constructs_cost_dependencies(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "preview.db"
    run_async(_seed_snapshot(path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        run,
        "caller_factory",
        lambda *args: pytest.fail("preview constructed a caller"),
    )
    monkeypatch.setattr(
        run,
        "http_client_factory",
        lambda: pytest.fail("preview constructed an HTTP client"),
    )
    monkeypatch.setattr(
        run,
        "browser_factory",
        lambda enabled: pytest.fail("preview constructed a browser"),
    )
    main(["preview", "-c", "acme", "--by-company", "--database", str(path)])
    output = capsys.readouterr().out
    assert "new                    1" in output
    assert "would process          1" in output
    assert COMPANY.slug in output


def test_run_dry_run_reuses_preview_without_factories(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "dry-run.db"
    run_async(_seed_snapshot(path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(run, "load_dotenv", lambda: False)
    monkeypatch.setattr(
        run,
        "LiteLLMCaller",
        lambda *args, **kwargs: pytest.fail("dry-run constructed a caller"),
    )
    monkeypatch.setattr(
        run,
        "http_client_factory",
        lambda: pytest.fail("dry-run constructed an HTTP client"),
    )
    monkeypatch.setattr(
        run,
        "browser_factory",
        lambda enabled: pytest.fail("dry-run constructed a browser"),
    )
    main(["run", "-c", "acme", "--dry-run", "--database", str(path)])
    assert "would process          1" in capsys.readouterr().out


def test_run_missing_provider_key_does_not_consume_work(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "missing-key.db"
    factory = run_async(_seed_snapshot(path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(run, "load_dotenv", lambda: False)

    with pytest.raises(SystemExit) as exc:
        main(["run", "-c", "acme", "--database", str(path)])

    async def state() -> tuple[int, int]:
        async with factory() as session:
            jobs = (await session.execute(select(models.Job))).scalars().all()
            attempts = (
                (await session.execute(select(models.JobAttempt))).scalars().all()
            )
            return len(jobs), len(attempts)

    assert exc.value.code == 2
    error = capsys.readouterr().err
    assert "OpenAI" in error
    assert "OPENAI_API_KEY" in error
    assert run_async(state()) == (0, 0)


def test_preview_no_observe_does_not_create_pipeline_jobs(
    catalog: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "no-observe.db"
    factory = run_async(_seed_snapshot(path))
    main(
        [
            "preview",
            "-c",
            "acme",
            "--no-observe",
            "--database",
            str(path),
        ]
    )

    async def count() -> int:
        async with factory() as session:
            return len((await session.execute(select(models.Job))).scalars().all())

    assert run_async(count()) == 0
    assert "would process          0" in capsys.readouterr().out


def test_migration_preflight_exits_two(
    catalog: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "empty.db"
    with pytest.raises(SystemExit) as exc:
        main(["preview", "--all", "--database", str(path)])
    assert exc.value.code == 2
    error = capsys.readouterr().err
    assert "alembic upgrade head" in error
    assert f"JOB_DISCOVERY_DB={path}" in error


async def _seed_audit(path: Path) -> tuple[async_sessionmaker[AsyncSession], int]:
    factory = await _database(path)
    async with factory() as session:
        await jobs_repo.observe(
            session, COMPANY.slug, [(URL, URL)], datetime(2026, 1, 1)
        )
    async with factory() as session:
        job = (await session.execute(select(models.Job))).scalar_one()
        job_id = job.id
    async with factory() as session:
        await runs_repo.start_run(
            session,
            run_id="seed",
            selection=COMPANY.slug,
            model="test",
            started_at=datetime(2026, 1, 1),
        )
    async with factory() as session:
        attempt_id = await attempts_repo.start(
            session,
            job_id=job_id,
            run_id="seed",
            trigger="scheduled",
            reason=None,
            model="test",
            policy_version="policy",
            extractor_version="extractor",
            started_at=datetime(2026, 1, 1),
        )
    async with factory() as session:
        content_id = await contents_repo.store(
            session,
            job_id,
            contents_repo.AcquiredContent(
                content_hash="hash",
                fingerprint="fingerprint",
                requisition_id="REQ-1",
                resolved_url=URL,
                http_status=200,
                method="main_content",
                title="Engineer",
                text="SECRET FULL POSTING TEXT for a Costa Rica software engineer.",
                jsonld_json='{"private":"posting"}',
                retrieved_at=datetime(2026, 1, 1),
            ),
        )
    async with factory() as session, session.begin():
        current_job = await session.get(models.Job, job_id)
        assert current_job is not None
        current_job.current_content_id = content_id
    async with factory() as session:
        await decisions_repo.accept(
            session,
            job_id=job_id,
            attempt_id=attempt_id,
            content_id=content_id,
            decision=decisions_repo.DecisionInput(
                "eligible", "Costa Rica.", '["Costa Rica"]', "policy"
            ),
            decided_at=datetime(2026, 1, 1),
        )
    async with factory() as session:
        await results_repo.accept(
            session,
            job_id=job_id,
            attempt_id=attempt_id,
            content_id=content_id,
            result=results_repo.ResultInput("extractor", 1, '{"title":"Engineer"}'),
            accepted_at=datetime(2026, 1, 1),
        )
    async with factory() as session:
        await attempts_repo.finish(
            session,
            attempt_id,
            phase_reached="extraction",
            outcome="completed_eligible",
            prompt_tokens=2,
            completion_tokens=3,
            finished_at=datetime(2026, 1, 1),
        )
    async with factory() as session:
        await jobs_repo.release(
            session,
            job_id,
            status="completed_eligible",
            next_phase="extraction",
            next_attempt_after=None,
        )
    return factory, job_id


def test_show_prints_audit_metadata_without_posting_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "show.db"
    _, job_id = run_async(_seed_audit(path))
    main(["show", "--job", str(job_id), "--database", str(path)])
    output = capsys.readouterr().out
    assert "Content versions" in output
    assert "Attempts" in output
    assert "evidence" in output
    assert '"title": "Engineer"' in output
    assert "SECRET FULL POSTING TEXT" not in output
    assert '"private":"posting"' not in output


def test_show_url_matches_exact_source_and_reports_ambiguity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "show-url.db"
    factory, job_id = run_async(_seed_audit(path))
    similar_url = f"{URL}?source=email"

    async def seed_sources() -> int:
        async with factory() as session:
            await jobs_repo.observe(
                session,
                COMPANY.slug,
                [(similar_url, similar_url)],
                datetime(2026, 1, 2),
            )
        async with factory() as session:
            similar_job = (await jobs_repo.list_by_url(session, similar_url))[0]
        async with factory() as session, session.begin():
            session.add(
                models.JobSource(
                    job_id=similar_job.id,
                    url=URL,
                    first_seen_at=datetime(2026, 1, 2),
                    last_seen_at=datetime(2026, 1, 2),
                )
            )
        return similar_job.id

    similar_job_id = run_async(seed_sources())
    main(["show", "--url", similar_url, "--database", str(path)])
    exact_output = capsys.readouterr().out
    assert exact_output.startswith(f"Job\n  id: {similar_job_id}\n")

    with pytest.raises(SystemExit) as exc:
        main(["show", "--url", URL, "--database", str(path)])
    assert exc.value.code == 2
    error = capsys.readouterr().err
    assert "URL matches multiple jobs" in error
    assert f"{job_id}, {similar_job_id}" in error


def test_reprocess_validates_all_ids_and_preserves_exact_phase_history(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "reprocess.db"
    factory, job_id = run_async(_seed_audit(path))
    captured: dict[str, Any] = {}

    async def fake_run_selected(
        args: argparse.Namespace,
        companies: list[Company],
        *,
        reprocess: tuple[int, ...],
        reason: str,
    ) -> None:
        captured.update(
            companies=companies,
            ids=reprocess,
            reason=reason,
            phase=args.from_phase,
        )

    monkeypatch.setattr(run, "run_selected", fake_run_selected)
    main(
        [
            "reprocess",
            "--job",
            str(job_id),
            "--reason",
            "policy change",
            "--from-phase",
            "extraction",
            "--database",
            str(path),
        ]
    )

    async def state() -> tuple[str, str, bool, bool]:
        async with factory() as session:
            job = await jobs_repo.get(session, job_id)
            decision = await decisions_repo.current(session, job_id)
            result = await results_repo.current(session, job_id)
        assert job is not None
        return job.status, job.next_phase, decision is not None, result is not None

    assert captured == {
        "companies": [COMPANY],
        "ids": (job_id,),
        "reason": "policy change",
        "phase": "extraction",
    }
    assert run_async(state()) == ("retry", "extraction", True, False)

    async def retry_budget() -> tuple[int, bool, bool]:
        async with factory() as session:
            job = await jobs_repo.get(session, job_id)
        assert job is not None
        return (
            job.attempt_count,
            job.next_attempt_after is not None,
            job.reprocess_requested,
        )

    assert run_async(retry_budget()) == (0, True, True)

    with pytest.raises(SystemExit) as exc:
        main(
            [
                "reprocess",
                "--job",
                str(job_id),
                "--job",
                "999",
                "--reason",
                "retry",
                "--database",
                str(path),
            ]
        )
    assert exc.value.code == 2


def test_reprocess_status_unknown_is_scoped_to_selected_company(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "reprocess-status.db"
    factory = run_async(_database(path))

    async def seed_unknowns() -> tuple[int, int]:
        async with factory() as session, session.begin():
            session.add(
                discovery_models.Company(
                    slug=OTHER_COMPANY.slug,
                    name=OTHER_COMPANY.name,
                    job_board_url=OTHER_COMPANY.job_board_url,
                    strategy=OTHER_COMPANY.strategy,
                    synced_at=datetime(2026, 1, 1),
                )
            )
        for company, url in (
            (COMPANY, URL),
            (OTHER_COMPANY, OTHER_COMPANY.sample_job_url),
        ):
            async with factory() as session:
                await jobs_repo.observe(
                    session,
                    company.slug,
                    [(url, url)],
                    datetime(2026, 1, 1),
                )
        async with factory() as session:
            acme = (await jobs_repo.list_by_status(session, COMPANY.slug, "new"))[0]
            beta = (await jobs_repo.list_by_status(session, OTHER_COMPANY.slug, "new"))[
                0
            ]
        for job_id in (acme.id, beta.id):
            async with factory() as session:
                await jobs_repo.release(
                    session,
                    job_id,
                    status="unknown",
                    next_phase="eligibility",
                    next_attempt_after=None,
                )
        return acme.id, beta.id

    acme_id, beta_id = run_async(seed_unknowns())
    captured: dict[str, Any] = {}

    async def fake_run_selected(
        args: argparse.Namespace,
        companies: list[Company],
        *,
        reprocess: tuple[int, ...],
        reason: str,
    ) -> None:
        captured.update(companies=companies, ids=reprocess, reason=reason)

    monkeypatch.setattr(run, "run_selected", fake_run_selected)
    main(
        [
            "reprocess",
            "--status",
            "unknown",
            "-c",
            "acme",
            "--reason",
            "policy review",
            "--database",
            str(path),
        ]
    )

    async def statuses() -> tuple[str, str]:
        async with factory() as session:
            acme = await jobs_repo.get(session, acme_id)
            beta = await jobs_repo.get(session, beta_id)
        assert acme is not None and beta is not None
        return acme.status, beta.status

    assert captured == {
        "companies": [COMPANY],
        "ids": (acme_id,),
        "reason": "policy review",
    }
    assert run_async(statuses()) == ("retry", "unknown")


def test_completed_reprocess_retains_history_and_replaces_current_rows(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "completed-reprocess.db"
    factory, job_id = run_async(_seed_audit(path))
    monkeypatch.setattr(run, "caller_factory", lambda model, timeout: AcceptedCaller())
    monkeypatch.setattr(run, "http_client_factory", _null_http)
    monkeypatch.setattr(run, "browser_factory", lambda enabled: _null_browser(enabled))

    main(
        [
            "reprocess",
            "--job",
            str(job_id),
            "--from-phase",
            "eligibility",
            "--reason",
            "policy review",
            "--model",
            "test-model",
            "--database",
            str(path),
        ]
    )

    async def state() -> tuple[Any, ...]:
        async with factory() as session:
            job = await jobs_repo.get(session, job_id)
            attempts = await attempts_repo.history(session, job_id)
            decisions = await decisions_repo.history(session, job_id)
            results = await results_repo.history(session, job_id)
        assert job is not None
        return job, attempts, decisions, results

    job, attempts, decisions, results = run_async(state())
    assert job.status == "completed_eligible"
    assert job.next_phase == "extraction"
    assert job.current_attempt_id is None
    assert job.current_content_id == decisions[-1].content_id == results[-1].content_id
    assert len(attempts) == len(decisions) == len(results) == 2
    assert attempts[-1].trigger == "reprocess"
    assert attempts[-1].reason == "policy review"
    assert attempts[-1].outcome == "completed_eligible"
    assert [decision.is_current for decision in decisions] == [False, True]
    assert [result.is_current for result in results] == [False, True]
    assert decisions[0].attempt_id == results[0].attempt_id == attempts[0].id
    assert decisions[1].attempt_id == results[1].attempt_id == attempts[1].id


def test_invalid_reprocess_prerequisites_do_not_mutate_any_selected_job(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "invalid-phase.db"
    factory, completed_id = run_async(_seed_audit(path))

    async def seed_without_checkpoint() -> int:
        raw_url = f"{URL}?missing=checkpoint"
        async with factory() as session:
            await jobs_repo.observe(
                session,
                COMPANY.slug,
                [(raw_url, raw_url)],
                datetime(2026, 1, 2),
            )
        async with factory() as session:
            return (await jobs_repo.list_by_url(session, raw_url))[0].id

    missing_id = run_async(seed_without_checkpoint())
    monkeypatch.setattr(
        run,
        "run_selected",
        lambda *args, **kwargs: pytest.fail("invalid selection was processed"),
    )

    with pytest.raises(SystemExit) as exc:
        main(
            [
                "reprocess",
                "--job",
                str(completed_id),
                "--job",
                str(missing_id),
                "--from-phase",
                "eligibility",
                "--reason",
                "retry",
                "--database",
                str(path),
            ]
        )

    async def state() -> tuple[Any, ...]:
        async with factory() as session:
            completed = await jobs_repo.get(session, completed_id)
            missing = await jobs_repo.get(session, missing_id)
            decisions = await decisions_repo.history(session, completed_id)
            results = await results_repo.history(session, completed_id)
            attempts = await attempts_repo.history(session, completed_id)
        return completed, missing, decisions, results, attempts

    completed, missing, decisions, results, attempts = run_async(state())
    assert exc.value.code == 2
    assert f"job {missing_id} has no content checkpoint" in capsys.readouterr().err
    assert completed is not None and completed.status == "completed_eligible"
    assert completed.reprocess_requested is False
    assert missing is not None and missing.status == "new"
    assert len(attempts) == len(decisions) == len(results) == 1
    assert decisions[0].is_current is True
    assert results[0].is_current is True


@asynccontextmanager
async def _null_http() -> AsyncIterator[httpx.AsyncClient]:
    def no_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected HTTP request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(no_request)) as client:
        yield client


@asynccontextmanager
async def _null_browser(enabled: bool) -> AsyncIterator[None]:
    assert enabled is False
    yield None


def test_reprocess_missing_provider_key_preserves_job(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "reprocess-missing-key.db"
    factory, job_id = run_async(_seed_audit(path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(reprocess, "load_dotenv", lambda: False)

    with pytest.raises(SystemExit) as exc:
        main(
            [
                "reprocess",
                "--job",
                str(job_id),
                "--reason",
                "retry",
                "--database",
                str(path),
            ]
        )

    async def state() -> tuple[str, str, int, bool, bool]:
        async with factory() as session:
            job = await session.get(models.Job, job_id)
            attempts = (
                (await session.execute(select(models.JobAttempt))).scalars().all()
            )
            decision = await decisions_repo.current(session, job_id)
            result = await results_repo.current(session, job_id)
        assert job is not None
        return (
            job.status,
            job.next_phase,
            len(attempts),
            decision is not None,
            result is not None,
        )

    assert exc.value.code == 2
    error = capsys.readouterr().err
    assert "OpenAI" in error
    assert "OPENAI_API_KEY" in error
    assert run_async(state()) == (
        "completed_eligible",
        "extraction",
        1,
        True,
        True,
    )


def test_run_injects_factories_and_keeps_job_failure_exit_zero(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "run.db"
    factory = run_async(_seed_snapshot(path))
    constructed = {"caller": 0, "http": 0, "browser": 0}

    class UnusedCaller:
        async def complete(self, messages: Any, schema: Any) -> Any:
            raise AssertionError("404 must stop before the model")

    def fake_caller(model: str, timeout: float) -> StructuredCaller:
        constructed["caller"] += 1
        assert model
        assert timeout > 0
        return UnusedCaller()

    @asynccontextmanager
    async def fake_http() -> AsyncIterator[httpx.AsyncClient]:
        constructed["http"] += 1
        async with httpx.AsyncClient() as client:
            yield client

    @asynccontextmanager
    async def fake_browser(enabled: bool) -> AsyncIterator[None]:
        constructed["browser"] += 1
        assert enabled is False
        yield None

    monkeypatch.setattr(run, "caller_factory", fake_caller)
    monkeypatch.setattr(run, "http_client_factory", fake_http)
    monkeypatch.setattr(run, "browser_factory", fake_browser)
    with respx.mock(assert_all_called=True) as router:
        router.get(URL).mock(return_value=httpx.Response(404))
        main(["run", "-c", "acme", "--limit", "1", "--database", str(path)])
    assert constructed == {"caller": 1, "http": 1, "browser": 1}
    assert f"{'unavailable':<22} 1" in capsys.readouterr().out

    async def status() -> str:
        async with factory() as session:
            job = (await jobs_repo.list_by_url(session, URL))[0]
            return job.status

    assert run_async(status()) == "unavailable"


def test_run_accepts_result_and_writes_json(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "accepted.db"
    output = tmp_path / "json"
    run_async(_seed_snapshot(path))
    posting_text = (
        "Costa Rica remote software engineer. "
        "Python is required. " + "Build reliable software systems in Costa Rica. " * 30
    )
    posting = (
        f"<html><body><main><h1>Engineer</h1><p>{posting_text}</p></main></body></html>"
    )

    monkeypatch.setattr(run, "caller_factory", lambda model, timeout: AcceptedCaller())
    with respx.mock(assert_all_called=True) as router:
        router.get(URL).mock(return_value=httpx.Response(200, text=posting))
        main(
            [
                "run",
                "-c",
                "acme",
                "--database",
                str(path),
                "--json-output",
                str(output),
            ]
        )
    written = list(output.glob("*.json"))
    assert len(written) == 1
    payload = json.loads(written[0].read_text(encoding="utf-8"))
    assert payload["company_slug"] == COMPANY.slug
    assert payload["result"]["title"]["value"] == "software engineer"


def test_reprocess_requires_reason_and_company_for_status() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["reprocess", "--job", "1"])
    assert exc.value.code == 2
    args = parser.parse_args(
        ["reprocess", "--status", "unknown", "--reason", "policy change"]
    )
    assert args.company is None
