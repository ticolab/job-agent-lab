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
    ELIGIBILITY_PROMPT_HASH,
    MISSING_INFORMATION_REASON,
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
TEXT = "Engineer\nRemote\nPRIVATE POSTING BODY NOT FOR RECORDINGS"


@pytest.fixture
def tool() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts" / "replay_eligibility.py"
    spec = importlib.util.spec_from_file_location("replay_eligibility_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def response(
    *, bad: bool = False, inconsistent: bool = False, missing: bool = False
) -> EligibilityResponse:
    evidence = "not in posting SECRET" if bad else "Remote"
    if missing:
        return EligibilityResponse.model_validate(
            {
                "decision": "unknown",
                "reason": MISSING_INFORMATION_REASON,
                "supporting_evidence": [],
                "conflicting_evidence": [],
            }
        )
    return EligibilityResponse.model_validate(
        {
            "decision": "eligible",
            "reason": "The role is remote with no exclusion.",
            "supporting_evidence": [evidence],
            "conflicting_evidence": [evidence] if inconsistent else ["Engineer"],
        }
    )


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
            for run_id in ("selected", "empty"):
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
            for job_id in (1, 2):
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
                        title="private title",
                        text=TEXT,
                        retrieved_at=now,
                    )
                )
                session.add(
                    JobAttempt(
                        id=job_id,
                        job_id=job_id,
                        run_id="selected",
                        trigger="scheduled",
                        model="fake",
                        started_at=now,
                    )
                )
            session.flush()
            session.add(
                JobDecision(
                    job_id=1,
                    attempt_id=1,
                    content_id=1,
                    decision="eligible",
                    reason="Accepted.",
                    supporting_evidence_json='["Remote"]',
                    conflicting_evidence_json="[]",
                    prompt_hash=ELIGIBILITY_PROMPT_HASH,
                    is_current=True,
                    decided_at=now,
                )
            )
    finally:
        engine.dispose()
    return path


class FakeCaller:
    def __init__(self, outcomes: Sequence[EligibilityResponse | BaseException]) -> None:
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
            if isinstance(outcome, BaseException):
                raise outcome
            return Completion(
                parsed=schema.model_validate(outcome.model_dump()),
                raw_json=outcome.model_dump_json(),
                model="provider-model",
                prompt_tokens=100,
                completion_tokens=20,
                latency_ms=7,
            )
        finally:
            self.active -= 1


def test_selection_and_one_shot_replay_is_safe(
    tool: ModuleType,
    database: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tool, "PROJECT_OUTPUT", tmp_path)
    before = database.read_bytes()
    postings = tool.load_postings(database, "selected")
    assert [
        (posting.job_id, posting.accepted_decision, posting.accepted_prompt_hash)
        for posting in postings
    ] == [(1, "eligible", ELIGIBILITY_PROMPT_HASH), (2, None, None)]
    raw = tmp_path / "baseline.raw"
    recording = run_async(
        tool.replay(
            postings,
            FakeCaller(
                [response()] * 3
                + [response(inconsistent=True), response(missing=True)]
                + [response(bad=True)] * 5
            ),
            run_id="selected",
            model="fake",
            raw_directory=raw,
            replays=5,
            concurrency=1,
        )
    )
    assert database.read_bytes() == before
    assert recording.recording_version == 3
    assert recording.eligibility_prompt_hash == ELIGIBILITY_PROMPT_HASH
    grounded = recording.records[0]
    inconsistent = recording.records[3]
    missing = recording.records[4]
    ungrounded = recording.records[5]
    assert (
        grounded.status,
        grounded.decision,
        grounded.supporting_count,
        grounded.conflicting_count,
        grounded.missing_information,
    ) == ("grounded", "eligible", 1, 1, False)
    assert inconsistent.status == "inconsistent"
    assert inconsistent.decision is None
    assert inconsistent.structural_errors == [
        "$.conflicting_evidence[0] appears in both evidence buckets"
    ]
    assert (missing.status, missing.decision, missing.missing_information) == (
        "grounded",
        "unknown",
        True,
    )
    assert ungrounded.status == "ungrounded"
    assert [miss.path for miss in ungrounded.grounding_misses] == [
        "$.supporting_evidence[0]"
    ]
    assert ungrounded.grounding_misses[0].reason == "not_in_posting"
    assert ungrounded.grounding_misses[0].value_characters == len(
        "not in posting SECRET"
    )
    assert len(ungrounded.grounding_misses[0].value_sha256) == 64
    assert grounded.raw_response_path.startswith("baseline.raw/")
    assert all((path.stat().st_mode & 0o777) == 0o600 for path in raw.iterdir())
    with pytest.raises(ValueError, match="ignored output"):
        tool._write_sidecar(tmp_path.parent / "unsafe", "key", "{}")
    serialized = recording.model_dump_json()
    assert "PRIVATE POSTING BODY" not in serialized
    assert "not in posting SECRET" not in serialized


def test_uses_one_shot_prompt_and_schema(tool: ModuleType, database: Path) -> None:
    postings = tool.load_postings(database, "selected")
    caller = FakeCaller([response()] * 10)
    run_async(tool.replay(postings, caller, run_id="selected", model="fake", replays=5))
    assert caller.calls[0] == (build_messages(TEXT), EligibilityResponse)


def test_call_artifact_identity_includes_the_prompt_hash(
    tool: ModuleType, database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    posting = tool.load_postings(database, "selected")[0]
    current = tool._recording_key("fake", "selected", posting, 1)
    contract = tool._sidecar_contract(current, "fake", "selected", posting, 1)
    assert contract["eligibility_prompt_hash"] == ELIGIBILITY_PROMPT_HASH
    assert contract["content_hash"] == posting.content_hash
    monkeypatch.setattr(tool, "ELIGIBILITY_PROMPT_HASH", "changed")
    assert tool._recording_key("fake", "selected", posting, 1) != current


def test_errors_timeout_and_concurrency(tool: ModuleType, database: Path) -> None:
    postings = tool.load_postings(database, "selected")
    caller = FakeCaller(
        [
            ModelResponseError("fake", "provider secret"),
            response(),
            response(),
            ModelCallError("fake", "provider secret"),
            *([response()] * 6),
        ]
    )
    recording = run_async(
        tool.replay(
            postings, caller, run_id="selected", model="fake", replays=5, concurrency=2
        )
    )
    assert caller.maximum == 2
    assert [record.status for record in recording.records[:4]] == [
        "invalid_response",
        "grounded",
        "grounded",
        "model_error",
    ]
    assert "provider secret" not in recording.model_dump_json()

    class SlowCaller:
        async def complete(
            self, messages: Sequence[Mapping[str, str]], schema: type[T]
        ) -> Completion[T]:
            await asyncio.sleep(10)
            raise AssertionError

    timed = run_async(
        tool.replay(
            postings,
            SlowCaller(),
            run_id="selected",
            model="fake",
            replays=5,
            timeout=0.001,
        )
    )
    assert all(record.error_category == "timeout" for record in timed.records)


def test_load_postings_is_bound_to_requested_run_history(
    tool: ModuleType, database: Path
) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO pipeline_runs "
            "(id, stage, trigger, started_at, selection, model) "
            "VALUES ('later', 'eligibility', 'scheduled', '2026-01-02', 'all', 'fake')"
        )
        connection.execute(
            "INSERT INTO job_attempts "
            "(id, job_id, run_id, trigger, model, started_at) "
            "VALUES (10, 1, 'later', 'scheduled', 'fake', '2026-01-02')"
        )
        connection.execute(
            "INSERT INTO job_contents "
            "(id, job_id, content_hash, fingerprint, resolved_url, method, text, "
            "retrieved_at) VALUES "
            "(10, 1, 'later-hash', 'later-fingerprint', "
            "'https://acme.example/jobs/1-new', 'main', 'Later content', "
            "'2026-01-02')"
        )
        connection.execute("UPDATE jobs SET current_content_id = 10 WHERE id = 1")
        connection.execute("UPDATE job_decisions SET is_current = 0 WHERE id = 1")
        connection.execute(
            "INSERT INTO job_decisions "
            "(id, job_id, attempt_id, content_id, decision, reason, "
            "supporting_evidence_json, conflicting_evidence_json, prompt_hash, "
            "is_current, decided_at) VALUES "
            "(10, 1, 10, 10, 'unknown', 'later', '[]', '[]', 'later-hash', 1, "
            "'2026-01-02')",
        )
    postings = tool.load_postings(database, "selected")
    assert postings[0].content_id == 1
    assert postings[0].content_hash == "hash-1"
    assert postings[0].accepted_decision == "eligible"
    assert postings[0].accepted_prompt_hash == ELIGIBILITY_PROMPT_HASH


@pytest.mark.parametrize("value", ["0", "nan", "inf"])
def test_invalid_timeout(tool: ModuleType, value: str) -> None:
    with pytest.raises(SystemExit):
        tool.build_parser().parse_args(
            ["run", "--run", "selected", "--label", "x", "--timeout", value]
        )


def test_cli_no_overwrite_and_cleanup(
    tool: ModuleType, database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tool, "PROJECT_OUTPUT", tmp_path)
    caller = FakeCaller([response()] * 10)
    monkeypatch.setattr(tool, "load_dotenv", lambda: None)
    monkeypatch.setattr(tool, "require_provider_credential", lambda model: None)
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
        "5",
    ]
    assert tool.main(args) == 0
    aggregate = (
        tmp_path / f"{__import__('hashlib').sha256(b'baseline').hexdigest()}.json"
    )
    saved = aggregate.read_bytes()
    assert tool.Recording.model_validate_json(saved).recording_version == 3
    assert tool.main(args) == 0
    assert aggregate.read_bytes() == saved
    assert len(caller.calls) == 10


def test_cleanup_preserves_preexisting_files(
    tool: ModuleType, database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tool, "PROJECT_OUTPUT", tmp_path)
    monkeypatch.setattr(tool, "load_dotenv", lambda: None)
    monkeypatch.setattr(tool, "require_provider_credential", lambda model: None)
    monkeypatch.setattr(
        tool,
        "LiteLLMCaller",
        lambda model, timeout: FakeCaller(
            [response(), KeyboardInterrupt(), *([response()] * 8)]
        ),
    )
    monkeypatch.setattr(tool, "run_async", run_async)
    output = tmp_path / "run"
    output.mkdir()
    keep = output / "keep.json"
    keep.write_text("keep", encoding="utf-8")
    assert (
        tool.main(
            [
                "run",
                "--run",
                "selected",
                "--database",
                str(database),
                "--output",
                str(output),
                "--label",
                "failed",
                "--replays",
                "5",
                "--concurrency",
                "1",
            ]
        )
        == 130
    )
    assert keep.read_text(encoding="utf-8") == "keep"
    failed_key = __import__("hashlib").sha256(b"failed").hexdigest()
    assert not (output / f"{failed_key}.json").exists()
    raw = output / f"{failed_key}.raw"
    assert list(raw.glob("*.json"))
    assert not list(raw.glob(".*.reserved"))


def test_cli_returns_one_and_resumes_complete_error_artifacts_without_provider(
    tool: ModuleType,
    database: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tool, "PROJECT_OUTPUT", tmp_path)
    caller = FakeCaller([ModelCallError("fake", "secret")] * 10)
    monkeypatch.setattr(tool, "load_dotenv", lambda: None)
    monkeypatch.setattr(tool, "require_provider_credential", lambda model: None)
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
        "errors",
        "--replays",
        "5",
    ]
    assert tool.main(args) == 1
    assert len(caller.calls) == 10
    key = __import__("hashlib").sha256(b"errors").hexdigest()
    aggregate = tmp_path / f"{key}.json"
    aggregate.unlink()

    def forbidden(model: str) -> None:
        raise AssertionError("complete call artifacts must skip credentials")

    monkeypatch.setattr(tool, "require_provider_credential", forbidden)
    monkeypatch.setattr(
        tool,
        "LiteLLMCaller",
        lambda model, timeout: (_ for _ in ()).throw(
            AssertionError("complete call artifacts must skip provider")
        ),
    )
    assert tool.main(args) == 1
    assert aggregate.is_file()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("decision", "unknown"),
        ("supporting_count", 2),
        ("conflicting_count", 0),
        ("missing_information", True),
    ],
)
def test_resume_rejects_tampered_derived_summary(
    tool: ModuleType,
    database: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    monkeypatch.setattr(tool, "PROJECT_OUTPUT", tmp_path)
    postings = tool.load_postings(database, "selected")
    raw = tmp_path / field
    run_async(
        tool.replay(
            postings[:1],
            FakeCaller([response()] * 5),
            run_id="selected",
            model="fake",
            raw_directory=raw,
            replays=5,
        )
    )
    artifact = sorted(raw.glob("*.json"))[0]
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["record"][field] = value
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="summary does not match"):
        run_async(
            tool.replay(
                postings[:1],
                None,
                run_id="selected",
                model="fake",
                raw_directory=raw,
                replays=5,
            )
        )


def test_core_replay_rejects_less_than_five(tool: ModuleType, database: Path) -> None:
    postings = tool.load_postings(database, "selected")
    with pytest.raises(ValueError, match="at least 5"):
        run_async(
            tool.replay(
                postings,
                FakeCaller([]),
                run_id="selected",
                model="fake",
                replays=4,
            )
        )


def test_credential_failure_removes_reserved_output(
    tool: ModuleType, database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tool, "PROJECT_OUTPUT", tmp_path)
    monkeypatch.setattr(tool, "load_dotenv", lambda: None)
    monkeypatch.setattr(
        tool,
        "require_provider_credential",
        lambda model: (_ for _ in ()).throw(SystemExit(2)),
    )
    with pytest.raises(SystemExit):
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
    key = __import__("hashlib").sha256(b"baseline").hexdigest()
    assert not (tmp_path / f"{key}.json").exists()
    assert not (tmp_path / f"{key}.raw").exists()


def test_compare_is_safe_and_checks_compatibility(
    tool: ModuleType, database: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    postings = tool.load_postings(database, "selected")
    baseline = run_async(
        tool.replay(
            postings,
            FakeCaller([response(bad=True)] * 5 + [response()] * 5),
            run_id="selected",
            model="old",
            replays=5,
        )
    )
    new = run_async(
        tool.replay(
            postings,
            FakeCaller([response()] * 10),
            run_id="selected",
            model="new",
            replays=5,
        )
    )
    report = tool.compare(baseline, new)
    assert "Grounding failures: 5/10 calls; structurally inconsistent: 0." in report
    assert "Grounding failures: 0/10" in report
    assert "Agreement with accepted decisions: 0/0 grounded calls" in report
    assert "Agreement with accepted decisions: 5/5 grounded calls" in report
    assert "eligible/s1c1: 5" in report
    assert "PRIVATE POSTING BODY" not in report
    assert "not in posting SECRET" not in report
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    old_path.write_text(baseline.model_dump_json(), encoding="utf-8")
    new_path.write_text(new.model_dump_json(), encoding="utf-8")
    assert tool.main(["compare", str(old_path), str(new_path)]) == 0
    assert capsys.readouterr().out == report
    new.postings[0].content_hash = "different"
    with pytest.raises(ValueError, match="content checkpoints"):
        tool.compare(baseline, new)


def test_unsupported_and_malformed_recordings_are_rejected_safely(
    tool: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    other = tmp_path / "other.json"
    bad = tmp_path / "bad.json"
    other.write_text(
        json.dumps({"recording_version": 2, "secret": "sk-secret"}), encoding="utf-8"
    )
    bad.write_text(
        json.dumps({"recording_version": 3, "secret": "sk-secret"}), encoding="utf-8"
    )
    assert tool.main(["compare", str(other), str(bad)]) == 2
    error = capsys.readouterr().err
    assert "unsupported recording version" in error
    assert "sk-secret" not in error
    assert tool.main(["compare", str(bad), str(bad)]) == 2
    error = capsys.readouterr().err
    assert "invalid recording format" in error
    assert "sk-secret" not in error
