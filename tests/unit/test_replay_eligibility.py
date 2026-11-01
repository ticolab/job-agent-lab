from __future__ import annotations

import asyncio
import importlib.util
import json
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import TypeVar

import pytest
from pydantic import BaseModel
from sqlalchemy import URL, create_engine
from sqlalchemy.orm import Session

from job_discovery.persistence.models import Company
from job_eligibility import (
    POLICY_VERSION,
    PROMPT_HASH,
    EligibilityResponse,
    build_messages,
)
from job_pipeline.llm import Completion, ModelCallError, ModelResponseError
from job_pipeline.persistence.models import (
    Base,
    Job,
    JobAttempt,
    JobContent,
    JobDecision,
    PipelineRun,
)
from tests.unit.asyncio_harness import run_async

T = TypeVar("T", bound=BaseModel)
LINE = "Contractor · Remote in Argentina or Mexico · Full-time · 9-month project"
EXPLANATION = "The posting states 'Latin America' as the location."
TEXT = f"Latin America\n{LINE}\nRemote\nPRIVATE POSTING BODY NOT FOR RECORDINGS"


@pytest.fixture
def tool() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts" / "replay_eligibility.py"
    spec = importlib.util.spec_from_file_location("replay_eligibility_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "replay ?#.db"
    engine = create_engine(URL.create("sqlite", database=str(path)))
    now = datetime(2026, 1, 1)
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session, session.begin():
            session.add(
                Company(
                    slug="acme",
                    name="Acme",
                    job_board_url="https://acme.example/jobs",
                    strategy="dom",
                    synced_at=now,
                )
            )
            for run_id in ("selected", "other", "empty"):
                session.add(
                    PipelineRun(
                        id=run_id,
                        stage="eligibility",
                        trigger="scheduled",
                        started_at=now,
                        selection="all",
                        model="fake",
                    )
                )
            for job_id in (1, 2, 3):
                session.add(
                    Job(
                        id=job_id,
                        company_slug="acme",
                        url_key=f"job-{job_id}",
                        status="new",
                        next_phase="eligibility",
                        first_seen_at=now,
                        last_seen_at=now,
                        updated_at=now,
                        current_content_id=job_id,
                    )
                )
                session.add(
                    JobContent(
                        id=job_id,
                        job_id=job_id,
                        content_hash=f"hash-{job_id}",
                        fingerprint=f"fingerprint-{job_id}",
                        resolved_url=f"https://acme.example/jobs/{job_id}",
                        method="main",
                        title="Role | <script> * [link](bad)",
                        text=TEXT,
                        retrieved_at=now,
                    )
                )
                session.add(
                    JobAttempt(
                        id=job_id,
                        job_id=job_id,
                        run_id="selected" if job_id < 3 else "other",
                        trigger="scheduled",
                        model="fake",
                        policy_version=POLICY_VERSION,
                        extractor_version="extractor",
                        started_at=now,
                    )
                )
            session.add(
                JobAttempt(
                    id=4,
                    job_id=1,
                    run_id="selected",
                    trigger="scheduled",
                    model="fake",
                    policy_version=POLICY_VERSION,
                    extractor_version="extractor",
                    started_at=now,
                )
            )
            session.add(
                JobAttempt(
                    id=5,
                    job_id=2,
                    run_id="other",
                    trigger="scheduled",
                    model="fake",
                    policy_version=POLICY_VERSION,
                    extractor_version="extractor",
                    started_at=now,
                )
            )
            for job_id, attempt_id in ((1, 1), (2, 5)):
                session.add(
                    JobDecision(
                        job_id=job_id,
                        attempt_id=attempt_id,
                        content_id=job_id,
                        decision="eligible",
                        reason="Accepted.",
                        evidence_json="[]",
                        policy_version=POLICY_VERSION,
                        is_current=True,
                        decided_at=now,
                    )
                )
    finally:
        engine.dispose()
    return path


def response(*, bad: bool = False, all_arrays: bool = False) -> EligibilityResponse:
    quote = EXPLANATION if bad else "Remote"
    return EligibilityResponse.model_validate(
        {
            "decision": "eligible",
            "reason": "The location permits work.",
            "evidence": [quote],
            "signals": {
                "locations_named": [],
                "locations_named_evidence": [f"'{LINE}'"] if bad else [],
                "remote_scope": "none_stated",
                "remote_scope_evidence": ["bad scope"] if all_arrays else [],
                "exhaustive_country_list": None,
                "exhaustive_country_list_evidence": ["bad list"] if all_arrays else [],
                "work_authorization_required_in": [],
                "work_authorization_evidence": ["bad authorization"]
                if all_arrays
                else [],
            },
        }
    )


class FakeCaller:
    def __init__(self, outcomes: Sequence[EligibilityResponse | Exception]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[Sequence[Mapping[str, str]], type[BaseModel]]] = []
        self.active = 0
        self.maximum = 0

    async def complete(
        self, messages: Sequence[Mapping[str, str]], schema: type[T]
    ) -> Completion[T]:
        self.calls.append((messages, schema))
        outcome = self.outcomes.pop(0)
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        try:
            await asyncio.sleep(0)
            if isinstance(outcome, Exception):
                raise outcome
            return Completion(
                parsed=schema.model_validate(outcome.model_dump()),
                raw_json=outcome.model_dump_json(),
                model="fake",
                prompt_tokens=100,
                completion_tokens=20,
                latency_ms=7,
            )
        finally:
            self.active -= 1


def test_selection_and_read_only_replay(tool: ModuleType, database: Path) -> None:
    before = database.read_bytes()
    postings = tool.load_postings(database, "selected")
    assert [p.job_id for p in postings] == [1, 2]
    assert [p.accepted_decision for p in postings] == ["eligible", None]
    caller = FakeCaller(
        [
            response(),
            response(bad=True),
            response(),
            ModelCallError("fake", "sk-secret PRIVATE POSTING BODY"),
        ]
    )
    recording = run_async(
        tool.replay(
            postings,
            caller,
            run_id="selected",
            model="fake",
            replays=2,
            concurrency=1,
        )
    )
    assert database.read_bytes() == before
    assert recording.policy_version == POLICY_VERSION == "cr-2026.10.1"
    assert recording.prompt_hash == PROMPT_HASH
    assert recording.timestamp.endswith("+00:00")
    assert len(recording.records) == 4
    assert all(schema is EligibilityResponse for _, schema in caller.calls)
    assert caller.calls[0][0] == build_messages(TEXT)
    grounded, bad, _, error = recording.records
    assert grounded.status == "grounded"
    assert grounded.raw_decision == "eligible"
    assert grounded.validated_decision == "unknown"
    assert grounded.validation_notes
    assert grounded.raw_response_json is None
    assert grounded.prompt_tokens == 100
    assert grounded.completion_tokens == 20
    assert grounded.latency_ms == 7
    assert bad.status == "ungrounded"
    assert bad.raw_decision == "eligible"
    assert bad.validated_decision is None
    assert [(miss.path, miss.quote) for miss in bad.ungrounded] == [
        ("$.evidence[0]", EXPLANATION),
        ("$.signals.locations_named_evidence[0]", f"'{LINE}'"),
    ]
    assert bad.raw_response_json == response(bad=True).model_dump_json()
    assert error.status == "model_error"
    assert error.raw_response_json is None
    serialized = recording.model_dump_json()
    assert TEXT not in serialized
    assert "PRIVATE POSTING BODY" not in serialized
    assert "sk-secret" not in serialized


def test_all_evidence_arrays_and_normalization(tool: ModuleType) -> None:
    misses = tool.ungrounded_strings(response(bad=True, all_arrays=True), TEXT)
    assert [miss.path for miss in misses] == [
        "$.evidence[0]",
        "$.signals.locations_named_evidence[0]",
        "$.signals.remote_scope_evidence[0]",
        "$.signals.exhaustive_country_list_evidence[0]",
        "$.signals.work_authorization_evidence[0]",
    ]
    normalized = response().model_copy(update={"evidence": ["REMOTE IN COSTA RICA"]})
    assert tool.ungrounded_strings(normalized, "Remote\n IN\u00a0Costa Rica") == []


def test_errors_and_concurrency(tool: ModuleType, database: Path) -> None:
    postings = tool.load_postings(database, "selected")
    caller = FakeCaller(
        [
            ModelResponseError("fake", "secret body"),
            response(),
            response(),
            response(),
        ]
    )
    recording = run_async(
        tool.replay(
            postings,
            caller,
            run_id="selected",
            model="fake",
            replays=2,
            concurrency=2,
        )
    )
    assert caller.maximum == 2
    assert recording.records[0].status == "invalid_response"
    assert "secret body" not in recording.model_dump_json()

    class SlowCaller:
        async def complete(
            self, messages: Sequence[Mapping[str, str]], schema: type[T]
        ) -> Completion[T]:
            await asyncio.sleep(10)
            raise AssertionError("must time out")

    timed = run_async(
        tool.replay(
            postings,
            SlowCaller(),
            run_id="selected",
            model="fake",
            replays=1,
            timeout=0.001,
        )
    )
    assert all(
        r.status == "model_error" and r.error == "model call timed out"
        for r in timed.records
    )


@pytest.mark.parametrize("case", ["missing", "unknown", "empty", "checkpoint"])
def test_input_errors_precede_credentials_and_calls(
    tool: ModuleType,
    database: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    case: str,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("input validation must precede credentials and calls")

    monkeypatch.setattr(tool, "load_dotenv", forbidden)
    monkeypatch.setattr(tool, "LiteLLMCaller", forbidden)
    selected = "selected"
    if case == "checkpoint":
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE jobs SET current_content_id = NULL WHERE id = 2")
    elif case == "missing":
        database = tmp_path / "missing.db"
    else:
        selected = case
    assert (
        tool.main(
            [
                "run",
                "--run",
                selected,
                "--database",
                str(database),
                "--output",
                str(tmp_path / "output"),
                "--label",
                "baseline",
            ]
        )
        == 2
    )
    assert capsys.readouterr().err
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize(
    "flag,value",
    [
        ("--replays", "0"),
        ("--concurrency", "0"),
        ("--timeout", "0"),
        ("--timeout", "nan"),
        ("--timeout", "inf"),
        ("--label", "../baseline"),
        ("--label", "/baseline"),
        ("--label", ".."),
        ("--label", "a/b"),
        ("--label", "a\\b"),
    ],
)
def test_invalid_flags(tool: ModuleType, flag: str, value: str) -> None:
    with pytest.raises(SystemExit) as exc:
        tool.build_parser().parse_args(
            [
                "run",
                "--run",
                "selected",
                "--label",
                "baseline",
                flag,
                value,
            ]
        )
    assert exc.value.code == 2


def test_cli_run_and_no_overwrite(
    tool: ModuleType,
    database: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caller = FakeCaller([response(), response()])
    events = []
    monkeypatch.setattr(tool, "load_dotenv", lambda: events.append("dotenv"))
    monkeypatch.setattr(
        tool, "require_provider_credential", lambda model: events.append("credential")
    )
    monkeypatch.setattr(tool, "LiteLLMCaller", lambda model, timeout: caller)
    monkeypatch.setattr(tool, "run_async", run_async)
    args = [
        "run",
        "--run",
        "selected",
        "--database",
        str(database),
        "--output",
        str(tmp_path),
        "--label",
        "baseline",
        "--replays",
        "1",
    ]
    before = database.read_bytes()
    assert tool.main(args) == 0
    assert events == ["dotenv", "credential"]
    path = tmp_path / "baseline.json"
    saved = path.read_bytes()
    assert database.read_bytes() == before
    assert len(tool.Recording.model_validate_json(saved).records) == 2
    assert tool.main(args) == 2
    assert "different --label" in capsys.readouterr().err
    assert path.read_bytes() == saved
    assert events == ["dotenv", "credential"]
    assert len(caller.calls) == 2


def test_missing_credential_removes_reserved_output(
    tool: ModuleType,
    database: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tool, "load_dotenv", lambda: None)

    def missing(model: str) -> None:
        raise SystemExit(2)

    monkeypatch.setattr(tool, "require_provider_credential", missing)
    with pytest.raises(SystemExit) as exc:
        tool.main(
            [
                "run",
                "--run",
                "selected",
                "--database",
                str(database),
                "--output",
                str(tmp_path),
                "--label",
                "baseline",
            ]
        )
    assert exc.value.code == 2
    assert not (tmp_path / "baseline.json").exists()


def test_compare_counts_and_escaping(
    tool: ModuleType,
    database: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    postings = tool.load_postings(database, "selected")
    baseline = run_async(
        tool.replay(
            postings,
            FakeCaller([response(bad=True), response(), response(), response()]),
            run_id="selected",
            model="fake",
            replays=2,
            concurrency=1,
        )
    )
    new = run_async(
        tool.replay(
            postings,
            FakeCaller([response(), response(), response(), response()]),
            run_id="selected",
            model="fake",
            replays=2,
            concurrency=1,
        )
    )
    report = tool.compare(baseline, new)
    assert "Grounding failures: 1/4" in report
    assert "Grounding failures: 0/4" in report
    assert "Job 1: grounded 2/2" in report
    assert "Job 1: accepted eligible; unknown: 2" in report
    assert "Job 2: unknown: 2" in report
    assert "Mean prompt tokens: 100.00 (4 measured)" in report
    assert "mean completion tokens: 20.00 (4 measured)" in report
    assert "<script>" not in report
    assert "[link](bad)" not in report
    assert "&lt;script&gt;" in report
    assert "&#124;" in report
    assert "signals&#46;locations" in report
    assert "PRIVATE POSTING BODY" not in report
    old_path, new_path = tmp_path / "old.json", tmp_path / "new.json"
    old_path.write_text(baseline.model_dump_json(), "utf-8")
    new_path.write_text(new.model_dump_json(), "utf-8")
    assert tool.main(["compare", str(old_path), str(new_path)]) == 0
    assert capsys.readouterr().out == report
    new.run_id = "other"
    new_path.write_text(new.model_dump_json(), "utf-8")
    assert tool.main(["compare", str(old_path), str(new_path)]) == 2
    assert "different run ids" in capsys.readouterr().err
    new.run_id = "selected"
    new.postings[0].job_id = 99
    with pytest.raises(ValueError, match="different job sets"):
        tool.compare(baseline, new)


def test_malformed_comparison_is_sanitized(
    tool: ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"secret": "sk-secret-provider-key"}), "utf-8")
    assert tool.main(["compare", str(path), str(path)]) == 2
    assert "sk-secret" not in capsys.readouterr().err
