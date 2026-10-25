"""Tests for structured model calls and versioned recording replay."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TypeVar

import pytest
from pydantic import BaseModel, ConfigDict

from job_pipeline.llm import (
    Completion,
    LiteLLMCaller,
    ModelCallError,
    ModelResponseError,
    RecordingCaller,
    ReplayCaller,
    recording_key,
)
from tests.unit.asyncio_harness import run_async


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


ModelT = TypeVar("ModelT", bound=BaseModel)


class StubCaller:
    async def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        schema: type[ModelT],
    ) -> Completion[ModelT]:
        raw = '{"value":"yes"}'
        return Completion(
            parsed=schema.model_validate_json(raw),
            raw_json=raw,
            model="stub",
            prompt_tokens=12,
            completion_tokens=3,
            latency_ms=4,
        )


MESSAGES = [{"role": "user", "content": "untrusted data"}]


def test_litellm_caller_parses_response_and_passes_safe_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_acompletion(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "model": "provider-model",
            "choices": [{"message": {"content": '{"value":"yes"}'}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3},
        }

    monkeypatch.setattr("job_pipeline.llm.litellm.acompletion", fake_acompletion)
    completion = run_async(LiteLLMCaller("test", timeout=5).complete(MESSAGES, Answer))
    assert completion.parsed.value == "yes"
    assert completion.model == "provider-model"
    assert completion.prompt_tokens == 12
    assert completion.completion_tokens == 3
    assert captured["response_format"] is Answer
    assert captured["temperature"] == 0
    assert "tools" not in captured


def test_absent_or_zero_usage_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_acompletion(**kwargs: Any) -> dict[str, Any]:
        return {
            "choices": [{"message": {"content": '{"value":"yes"}'}}],
            "usage": {"prompt_tokens": 0},
        }

    monkeypatch.setattr("job_pipeline.llm.litellm.acompletion", fake_acompletion)
    completion = run_async(LiteLLMCaller("test", timeout=5).complete(MESSAGES, Answer))
    assert completion.prompt_tokens is None
    assert completion.completion_tokens is None


def test_refusal_is_model_response_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_acompletion(**kwargs: Any) -> dict[str, Any]:
        return {"choices": [{"message": {"refusal": "no"}}]}

    monkeypatch.setattr("job_pipeline.llm.litellm.acompletion", fake_acompletion)
    with pytest.raises(ModelResponseError) as exc_info:
        run_async(LiteLLMCaller("test", timeout=5).complete(MESSAGES, Answer))
    assert exc_info.value.model == "test"


def test_invalid_json_is_model_response_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_acompletion(**kwargs: Any) -> dict[str, Any]:
        return {"choices": [{"message": {"content": "not-json"}}]}

    monkeypatch.setattr("job_pipeline.llm.litellm.acompletion", fake_acompletion)
    with pytest.raises(ModelResponseError):
        run_async(LiteLLMCaller("test", timeout=5).complete(MESSAGES, Answer))


def test_provider_exception_is_model_call_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_acompletion(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("provider failed")

    monkeypatch.setattr("job_pipeline.llm.litellm.acompletion", fake_acompletion)
    with pytest.raises(ModelCallError) as exc_info:
        run_async(LiteLLMCaller("test", timeout=5).complete(MESSAGES, Answer))
    assert exc_info.value.model == "test"


def test_record_and_replay_round_trip(tmp_path: Path) -> None:
    recorded = run_async(
        RecordingCaller(StubCaller(), tmp_path).complete(MESSAGES, Answer)
    )
    replayed = run_async(ReplayCaller(tmp_path).complete(MESSAGES, Answer))
    assert replayed == recorded
    key = recording_key(MESSAGES, Answer)
    envelope = json.loads((tmp_path / f"{key}.json").read_text(encoding="utf-8"))
    assert envelope["version"] == 1
    assert envelope["key"] == key
    assert envelope["schema"] == "Answer"
    assert envelope["schema_json"] == Answer.model_json_schema()


def test_explicit_recording_key_round_trips(tmp_path: Path) -> None:
    run_async(
        RecordingCaller(StubCaller(), tmp_path).complete(
            MESSAGES, Answer, key="fixture"
        )
    )
    replayed = run_async(
        ReplayCaller(tmp_path).complete(MESSAGES, Answer, key="fixture")
    )
    assert replayed.parsed.value == "yes"


def test_replay_rejects_wrong_envelope_version(tmp_path: Path) -> None:
    key = recording_key(MESSAGES, Answer)
    (tmp_path / f"{key}.json").write_text(
        json.dumps({"version": 2, "key": key}), encoding="utf-8"
    )
    with pytest.raises(ModelResponseError, match="unsupported recording version"):
        run_async(ReplayCaller(tmp_path).complete(MESSAGES, Answer))
