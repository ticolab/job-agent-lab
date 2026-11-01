from __future__ import annotations

import _thread
import argparse
import asyncio
import json
import logging
import re
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
from job_pipeline.cli import common, eligibility, extraction, reprocess
from job_pipeline.cli import main as main_module
from job_pipeline.cli import stages as run
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
from tests.unit.test_pipeline_diagnostics import pipeline_logger as pipeline_logger

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


@pytest.mark.parametrize("command", ["eligibility", "extraction", "reprocess"])
@pytest.mark.parametrize("flag,count", [("-v", 1), ("-vv", 2), ("--verbose", 1)])
def test_stage_verbosity_parser(command: str, flag: str, count: int) -> None:
    selection = (
        ["--job", "1", "--reason", "test"] if command == "reprocess" else ["--all"]
    )
    assert _args(command, *selection, flag).verbose == count
    assert _args(command, *selection).verbose == 0


@pytest.mark.parametrize("command", ["preview", "show"])
def test_nonstage_commands_reject_verbosity(command: str) -> None:
    selection = ["--job", "1"] if command == "show" else ["--all"]
    with pytest.raises(SystemExit):
        _args(command, *selection, "-v")


def test_dispatcher_and_policy_flags_use_real_parser() -> None:
    parser = build_parser()
    assert {
        row.name
        for row in __import__(
            "job_pipeline.cli.main", fromlist=["SUBCOMMANDS"]
        ).SUBCOMMANDS
    } == {"preview", "run", "eligibility", "extraction", "reprocess", "show"}
    args = parser.parse_args(
        [
            "eligibility",
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
    assert "would process (eligibility) 1" in output
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
    monkeypatch.setattr(eligibility, "load_dotenv", lambda: False)
    monkeypatch.setattr(extraction, "load_dotenv", lambda: False)
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
    main(["eligibility", "-c", "acme", "--dry-run", "--database", str(path)])
    assert "would process (eligibility) 1" in capsys.readouterr().out


def test_run_missing_provider_key_does_not_consume_work(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "missing-key.db"
    factory = run_async(_seed_snapshot(path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(eligibility, "load_dotenv", lambda: False)
    monkeypatch.setattr(extraction, "load_dotenv", lambda: False)

    with pytest.raises(SystemExit) as exc:
        main(["eligibility", "-c", "acme", "--database", str(path)])

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
    assert "would process (eligibility) 0" in capsys.readouterr().out


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
            stage="eligibility",
            trigger="scheduled",
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
        stage: str,
        reprocess: tuple[int, ...],
        reason: str,
    ) -> None:
        captured.update(
            companies=companies,
            ids=reprocess,
            reason=reason,
            phase=args.from_phase,
        )

    monkeypatch.setattr(run, "run_stage", fake_run_selected)
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
        stage: str,
        reprocess: tuple[int, ...],
        reason: str,
    ) -> None:
        captured.update(companies=companies, ids=reprocess, reason=reason)

    monkeypatch.setattr(run, "run_stage", fake_run_selected)
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
    assert job.status == "ready_for_extraction"
    assert job.next_phase == "extraction"
    assert job.current_attempt_id is None
    assert job.attempt_count == 0
    assert job.current_content_id == decisions[-1].content_id
    assert len(attempts) == len(decisions) == 2
    assert len(results) == 1
    assert attempts[-1].trigger == "reprocess"
    assert attempts[-1].reason == "policy review"
    assert attempts[-1].outcome == "ready_for_extraction"
    assert [decision.is_current for decision in decisions] == [False, True]
    assert [result.is_current for result in results] == [False]
    assert decisions[0].attempt_id == results[0].attempt_id == attempts[0].id
    assert decisions[1].attempt_id == attempts[1].id


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
        "run_stage",
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
        main(["eligibility", "-c", "acme", "--limit", "1", "--database", str(path)])
    assert constructed == {"caller": 1, "http": 1, "browser": 1}
    assert f"{'unavailable':<22} 1" in capsys.readouterr().out

    async def status() -> str:
        async with factory() as session:
            job = (await jobs_repo.list_by_url(session, URL))[0]
            return job.status

    assert run_async(status()) == "unavailable"


@pytest.mark.parametrize("empty", [False, True])
def test_cli_diagnostics_preserve_stdout_and_exclude_payloads(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    pipeline_logger: logging.Logger,
    empty: bool,
) -> None:
    from job_pipeline.content import ladder
    from tests.unit.test_pipeline_worker import _fetch_with_public_dns

    monkeypatch.setattr(ladder, "fetch", _fetch_with_public_dns)
    monkeypatch.setattr(eligibility, "load_dotenv", lambda: False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-SECRETVALUE")
    monkeypatch.setattr(run, "caller_factory", lambda *args: AcceptedCaller())
    monkeypatch.setattr(run, "browser_factory", _null_browser)
    source = URL + "?token=abc123#private-fragment"
    posting = (
        "<html><main><p>tiny</p></main></html>"
        if empty
        else (
            "<html><main><p>BODY-MARKER-7f3a Costa Rica software engineer. "
            + "Build reliable systems. " * 40
            + "</p></main></html>"
        )
    )
    outputs: list[str] = []
    for verbosity in ([], ["-v"], ["-vv"]):
        path = tmp_path / f"diagnostic-{len(outputs)}.db"
        factory = run_async(_seed_snapshot(path))

        async def source_snapshot(
            factory: async_sessionmaker[AsyncSession] = factory,
        ) -> None:
            async with factory() as session:
                await discovery_jobs_repo.replace_company_urls(
                    session,
                    company_slug=COMPANY.slug,
                    urls=[source],
                    captured_at=datetime(2026, 1, 1),
                )

        run_async(source_snapshot())
        with respx.mock(assert_all_called=True) as router:
            router.get(source).mock(return_value=httpx.Response(200, text=posting))
            main(["eligibility", "-c", "acme", "--database", str(path), *verbosity])
        captured = capsys.readouterr()
        outputs.append(
            re.sub(r"\d{8}_\d{6}_[a-f0-9]+|\d+\.\d+s", "VARIABLE", captured.out)
        )
        assert "acquire run=" not in captured.out
        if not verbosity:
            assert captured.err == ""
        else:
            assert "acquire run=" in captured.err
            assert "job=1" in captured.err
            assert "page_chars=" in captured.err and "frames=0/0/0" in captured.err
            assert ("acquire step " in captured.err) == (verbosity == ["-vv"])
        for forbidden in (
            "SECRETVALUE",
            "BODY-MARKER-7f3a",
            "abc123",
            "private-fragment",
            "Authorization",
            "HTTP Request:",
        ):
            assert forbidden not in captured.err
    assert outputs[0] == outputs[1] == outputs[2]


def test_verbose_cli_framed_acquisition_logs_each_call_and_frame(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    pipeline_logger: logging.Logger,
) -> None:
    from typing import cast

    from job_discovery.settings import CaptureBrowser
    from job_pipeline.content import ladder
    from job_pipeline.content.render import FrameCapture, RenderedPage
    from tests.unit.test_pipeline_worker import _fetch_with_public_dns

    path = tmp_path / "framed-cli.db"
    run_async(_seed_snapshot(path))
    dom = COMPANY.model_copy(update={"strategy": "dom"})
    monkeypatch.setattr(common, "find_company", lambda handle: dom)
    monkeypatch.setattr(ladder, "fetch", _fetch_with_public_dns)
    monkeypatch.setattr(eligibility, "load_dotenv", lambda: False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-SECRETVALUE")
    monkeypatch.setattr(run, "caller_factory", lambda *args: AcceptedCaller())
    page = "<html><title>software engineer</title><main>Mexicali</main></html>"
    frame = (
        "<html><main><p>BODY-MARKER-7f3a Costa Rica software engineer. "
        + "Build reliable systems. " * 40
        + "</p></main></html>"
    )

    async def render_page(*args: object, **kwargs: object) -> RenderedPage:
        return RenderedPage(
            page,
            URL,
            1,
            (
                FrameCapture(
                    0,
                    "about:blank",
                    "about",
                    "candidate",
                    "view_html_iframe",
                    "Job Description",
                    frame,
                ),
            ),
        )

    @asynccontextmanager
    async def browser_factory(enabled: bool) -> AsyncIterator[CaptureBrowser]:
        assert enabled
        yield cast(CaptureBrowser, object())

    monkeypatch.setattr(run, "browser_factory", browser_factory)
    monkeypatch.setattr(ladder, "render_page", render_page)
    with respx.mock(assert_all_called=True) as router:
        router.get(URL).mock(return_value=httpx.Response(200, text=page))
        main(["eligibility", "-c", "acme", "--database", str(path), "-vv"])
    captured = capsys.readouterr()
    lines = captured.err.splitlines()
    assert sum("acquire run=" in line for line in lines) == 2
    assert "outcome=rendered_frame_main" in captured.err
    assert "frames=1/1/1 selected=0" in captured.err
    assert 'element_id="view_html_iframe" title="Job Description"' in captured.err
    assert "acquire run=" not in captured.out
    for forbidden in (
        "BODY-MARKER-7f3a",
        "SECRETVALUE",
        "Authorization",
        "HTTP Request:",
    ):
        assert forbidden not in captured.err


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
                "eligibility",
                "-c",
                "acme",
                "--database",
                str(path),
            ]
        )
    main(
        [
            "extraction",
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


@pytest.mark.parametrize(
    "extras",
    [
        [],
        ["--all"],
        ["-c", "acme", "--dry-run"],
        ["--bogus"],
        ["--help"],
        ["--limit", "bad"],
    ],
)
def test_retired_run_accepts_any_arguments_and_opens_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    extras: list[str],
) -> None:
    path = tmp_path / "never-created.db"
    with pytest.raises(SystemExit) as exc:
        main(["run", *extras, "--database", str(path)])
    assert exc.value.code == 2
    assert not path.exists()
    error = capsys.readouterr().err
    assert "job-pipeline eligibility" in error and "job-pipeline extraction" in error


@pytest.mark.parametrize(
    "argv",
    [
        ["extraction", "--http-concurrency", "5"],
        ["extraction", "--browser-concurrency", "1"],
        ["eligibility", "--json-output", "directory"],
        ["extraction", "--bogus"],
        ["eligibility", "--bogus"],
        ["extraction", "--limit", "0"],
    ],
)
def test_stage_parser_rejects_unsupported_flags(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main([*argv, "--all"])
    assert exc.value.code == 2


@pytest.mark.parametrize("stage", ["eligibility", "extraction"])
def test_stage_dry_runs_construct_nothing_and_extraction_is_read_only(
    catalog: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    path = tmp_path / "dry-stages.db"
    factory = run_async(_seed_snapshot(path))

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("dry run constructed resources")

    for name in ("caller_factory", "http_client_factory", "browser_factory"):
        monkeypatch.setattr(run, name, forbidden)

    async def counts() -> dict[str, int]:
        async with factory() as session:
            return {
                table.name: len((await session.execute(select(table))).all())
                for table in models.Base.metadata.sorted_tables
            }

    before = run_async(counts())
    main([stage, "-c", "acme", "--dry-run", "--database", str(path)])
    after = run_async(counts())
    if stage == "extraction":
        assert before == after
    else:
        assert after["jobs"] == 1
        assert after["pipeline_runs"] == after["job_attempts"] == 0


@pytest.mark.parametrize("stage", ["eligibility", "extraction"])
def test_stage_missing_key_writes_no_rows(
    catalog: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    path = tmp_path / "key.db"
    factory = run_async(_seed_snapshot(path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(eligibility, "load_dotenv", lambda: False)
    monkeypatch.setattr(extraction, "load_dotenv", lambda: False)
    with pytest.raises(SystemExit) as exc:
        main([stage, "-c", "acme", "--database", str(path)])
    assert exc.value.code == 2

    async def counts() -> tuple[int, int, int]:
        async with factory() as session:
            counts = [
                len((await session.execute(select(table))).all())
                for table in (models.Job, models.JobAttempt, models.PipelineRun)
            ]
            return counts[0], counts[1], counts[2]

    assert run_async(counts()) == (0, 0, 0)


def test_extraction_dom_selection_never_constructs_fetch_resources(
    catalog: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "dom.db"
    factory, job_id = run_async(_seed_audit(path))
    dom = COMPANY.model_copy(update={"strategy": "dom"})
    monkeypatch.setattr(common, "find_company", lambda handle: dom)
    monkeypatch.setattr(run, "caller_factory", lambda *args: AcceptedCaller())

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("extraction constructed fetch resources")

    monkeypatch.setattr(run, "http_client_factory", forbidden)
    monkeypatch.setattr(run, "browser_factory", forbidden)

    async def ready() -> None:
        async with factory() as session:
            await jobs_repo.release(
                session,
                job_id,
                status="ready_for_extraction",
                next_phase="extraction",
                next_attempt_after=None,
            )
        async with factory() as session:
            await discovery_jobs_repo.replace_company_urls(
                session,
                company_slug=COMPANY.slug,
                urls=[URL],
                captured_at=datetime(2026, 1, 1),
            )

    run_async(ready())
    main(["extraction", "-c", "acme", "--database", str(path)])

    async def state() -> None:
        async with factory() as session:
            job = await jobs_repo.get(session, job_id)
            runs = (
                (
                    await session.execute(
                        select(models.PipelineRun).where(
                            models.PipelineRun.id != "seed"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert job is not None and job.status == "completed_eligible"
        assert [(row.stage, row.trigger) for row in runs] == [
            ("extraction", "scheduled")
        ]

    run_async(state())


def test_show_waiting_state_and_run_identity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "waiting.db"
    factory, job_id = run_async(_seed_audit(path))

    async def ready() -> None:
        async with factory() as session:
            await jobs_repo.release(
                session,
                job_id,
                status="ready_for_extraction",
                next_phase="extraction",
                next_attempt_after=None,
            )

    run_async(ready())
    main(["show", "--job", str(job_id), "--database", str(path)])
    output = capsys.readouterr().out
    assert "eligible; waiting for `job-pipeline extraction`" in output
    assert "attempts in current stage: 0" in output
    assert "run_stage: eligibility" in output
    assert "run_trigger: scheduled" in output
    assert "SECRET FULL POSTING TEXT" not in output


@pytest.mark.parametrize("phase", ["fetch", "eligibility"])
def test_historical_reprocess_stops_then_requires_explicit_extraction(
    catalog: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    path = tmp_path / "historical.db"
    factory, job_id = run_async(_seed_audit(path))
    caller = AcceptedCaller()
    monkeypatch.setattr(run, "caller_factory", lambda *args: caller)
    monkeypatch.setattr(run, "http_client_factory", _null_http)
    monkeypatch.setattr(run, "browser_factory", _null_browser)
    from job_pipeline.content.model import AcquiredContent

    async def acquire(*args: object, **kwargs: object) -> AcquiredContent:
        return AcquiredContent(
            "Costa Rica software engineer. " * 50,
            "Engineer",
            URL,
            200,
            "main_content",
            None,
            None,
            datetime(2026, 1, 2),
            "new-hash",
        )

    monkeypatch.setattr("job_pipeline.runner.worker.acquire", acquire)
    base = [
        "reprocess",
        "--job",
        str(job_id),
        "--reason",
        "review",
        "--database",
        str(path),
    ]
    main([*base, "--from-phase", phase])

    async def waiting() -> None:
        async with factory() as session:
            job = await jobs_repo.get(session, job_id)
            attempts = await attempts_repo.history(session, job_id)
            results = await results_repo.history(session, job_id)
            decisions = await decisions_repo.history(session, job_id)
        assert job is not None and job.status == "ready_for_extraction"
        assert len(attempts) == len(decisions) == 2
        assert len(results) == 1 and not results[0].is_current
        assert attempts[-1].phase_reached == "eligibility"

    run_async(waiting())
    main(["extraction", "-c", "acme", "--database", str(path)])
    run_async(waiting())
    main([*base, "--from-phase", "extraction"])

    async def completed() -> None:
        async with factory() as session:
            job = await jobs_repo.get(session, job_id)
            decisions = await decisions_repo.history(session, job_id)
            results = await results_repo.history(session, job_id)
            runs = (
                (
                    await session.execute(
                        select(models.PipelineRun).where(
                            models.PipelineRun.trigger == "reprocess"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert job is not None and job.status == "completed_eligible"
        assert len(decisions) == len(results) == 2
        assert [row.is_current for row in results] == [False, True]
        assert {row.stage for row in runs} == {"eligibility", "extraction"}

    run_async(completed())


@pytest.mark.parametrize("from_phase", ["fetch", "eligibility", "extraction"])
@pytest.mark.parametrize("no_observe", [False, True])
def test_preview_excludes_prepared_unclaimed_reprocess(
    catalog: None,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    from_phase: str,
    no_observe: bool,
) -> None:
    from job_discovery.persistence.timestamps import utc_now

    path = tmp_path / "prepared-reprocess.db"
    factory, job_id = run_async(_seed_audit(path))
    stage: models.Stage = "extraction" if from_phase == "extraction" else "eligibility"

    async def prepare() -> None:
        async with factory() as session:
            await discovery_jobs_repo.replace_company_urls(
                session,
                company_slug=COMPANY.slug,
                urls=[URL],
                captured_at=utc_now(),
            )
        async with factory() as session:
            assert await jobs_repo.prepare_reprocess(
                session, [job_id], from_phase=from_phase
            ) == (job_id,)
        keys = {(COMPANY.slug, URL)}
        for selected_stage in models.STAGE_PHASES:
            async with factory() as session:
                assert (
                    await jobs_repo.list_work(
                        session, keys, utc_now(), None, stage=selected_stage
                    )
                    == ()
                )
            async with factory() as session:
                assert not await jobs_repo.claim(session, job_id, stage=selected_stage)
        async with factory() as session:
            work = await jobs_repo.list_work(
                session, keys, utc_now(), None, stage=stage, reprocess=True
            )
            assert [job.id for job in work] == [job_id]

    run_async(prepare())
    main(
        [
            "preview",
            "-c",
            "acme",
            "--by-company",
            "--database",
            str(path),
            *(["--no-observe"] if no_observe else []),
        ]
    )
    output = capsys.readouterr().out
    assert output.count("would process (eligibility) 0") == 2
    assert output.count("would process (extraction) 0") == 2
    assert output.count("retry due              1") == 2

    async def unchanged() -> None:
        async with factory() as session:
            job = await jobs_repo.get(session, job_id)
            assert job is not None
            assert job.status == "retry"
            assert job.next_phase == from_phase
            assert job.reprocess_requested
            assert job.current_attempt_id is None
            assert job.attempt_count == 0

    run_async(unchanged())


def test_preview_stage_sections_limit_company_and_merged_alias(
    catalog: None,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from datetime import timedelta

    from job_discovery.persistence.timestamps import utc_now

    path = tmp_path / "dispositions.db"
    factory = run_async(_database(path))
    dispositions = [
        ("new", "fetch", None),
        ("retry", "fetch", -1),
        ("retry", "eligibility", 1),
        ("ready_for_extraction", "extraction", None),
        ("retry", "extraction", -1),
        ("retry", "extraction", 1),
        ("in_progress", "eligibility", None),
        *(
            (status, "eligibility", None)
            for status in (
                "completed_eligible",
                "completed_ineligible",
                "unknown",
                "unavailable",
                "failed",
            )
        ),
    ]

    async def seed() -> None:
        now = utc_now()
        urls = [f"{URL}{index}" for index in range(len(dispositions) + 1)]
        async with factory() as session:
            await jobs_repo.observe(
                session, COMPANY.slug, [(url, url) for url in urls], now
            )
        async with factory() as session, session.begin():
            jobs = (
                (await session.execute(select(models.Job).order_by(models.Job.id)))
                .scalars()
                .all()
            )
            for job, (status, phase, due) in zip(jobs, dispositions, strict=False):
                job.status = status
                job.next_phase = phase
                job.next_attempt_after = (
                    now + timedelta(days=due) if due is not None else None
                )
            jobs[-1].status = "merged"
            jobs[-1].merged_into_id = jobs[3].id
        async with factory() as session:
            await discovery_jobs_repo.replace_company_urls(
                session,
                company_slug=COMPANY.slug,
                urls=[url for index, url in enumerate(urls) if index != 3],
                captured_at=now,
            )

    run_async(seed())
    main(
        [
            "preview",
            "-c",
            "acme",
            "--no-observe",
            "--by-company",
            "--limit",
            "1",
            "--database",
            str(path),
        ]
    )
    output = capsys.readouterr().out
    for title in (
        "Eligibility work",
        "Extraction backlog",
        "In progress",
        "Terminal dispositions",
    ):
        assert output.count(title) == 2
    assert output.count("would process (eligibility) 1") == 2
    assert output.count("would process (extraction) 1") == 2
    assert "ready for extraction   1" in output
    assert "retry due              1" in output
    assert "retry waiting          1" in output
