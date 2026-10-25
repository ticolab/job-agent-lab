"""Structured LiteLLM adapter plus deterministic recording and replay callers."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypeVar

import litellm
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)
RECORDING_VERSION = 1


@dataclass(frozen=True)
class Completion[T: BaseModel]:
    """A parsed structured completion and its provider metadata."""

    parsed: T
    raw_json: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: int


class StructuredCaller(Protocol):
    """Port used by leaves that request structured model output."""

    async def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        schema: type[T],
    ) -> Completion[T]: ...


class ModelCallError(RuntimeError):
    """A provider call failed before a usable response was returned."""

    def __init__(self, model: str, message: str) -> None:
        self.model = model
        super().__init__(f"{model}: {message}")


class ModelResponseError(RuntimeError):
    """A provider returned a refusal or invalid structured output."""

    def __init__(self, model: str, message: str) -> None:
        self.model = model
        super().__init__(f"{model}: {message}")


def recording_key(
    messages: Sequence[Mapping[str, str]], schema: type[BaseModel]
) -> str:
    """Return a stable key for one prompt and response-schema contract."""
    payload = {
        "messages": [dict(message) for message in messages],
        "schema": schema.model_json_schema(),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _value(obj: object, name: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def _positive_token_count(usage: object | None, name: str) -> int | None:
    value = _value(usage, name) if usage is not None else None
    return value if isinstance(value, int) and value > 0 else None


class LiteLLMCaller:
    """Call one LiteLLM model with Pydantic structured output."""

    def __init__(
        self,
        model: str,
        *,
        timeout: float,
        max_retries: int = 1,
    ) -> None:
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries

    async def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        schema: type[T],
    ) -> Completion[T]:
        started = time.monotonic()
        try:
            response = await litellm.acompletion(
                model=self.model,
                messages=[dict(message) for message in messages],
                response_format=schema,
                temperature=0,
                timeout=self.timeout,
                max_retries=self.max_retries,
            )
        except Exception as exc:
            raise ModelCallError(self.model, str(exc)) from exc

        latency_ms = round((time.monotonic() - started) * 1000)
        choices = _value(response, "choices")
        if not isinstance(choices, Sequence) or not choices:
            raise ModelResponseError(self.model, "response contained no choices")
        message = _value(choices[0], "message")
        refusal = _value(message, "refusal")
        if refusal:
            raise ModelResponseError(self.model, f"model refused: {refusal}")
        raw_json = _value(message, "content")
        if not isinstance(raw_json, str) or not raw_json.strip():
            raise ModelResponseError(self.model, "response contained no JSON content")
        try:
            parsed = schema.model_validate_json(raw_json)
        except (ValidationError, ValueError) as exc:
            raise ModelResponseError(
                self.model, f"invalid structured response: {exc}"
            ) from exc

        usage = _value(response, "usage")
        response_model = _value(response, "model")
        return Completion(
            parsed=parsed,
            raw_json=raw_json,
            model=response_model if isinstance(response_model, str) else self.model,
            prompt_tokens=_positive_token_count(usage, "prompt_tokens"),
            completion_tokens=_positive_token_count(usage, "completion_tokens"),
            latency_ms=latency_ms,
        )


class RecordingCaller:
    """Wrap a caller and persist each completion in a versioned envelope."""

    def __init__(
        self,
        caller: StructuredCaller,
        directory: Path,
        *,
        key: str | None = None,
    ) -> None:
        self.caller = caller
        self.directory = directory
        self.key = key

    async def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        schema: type[T],
        *,
        key: str | None = None,
    ) -> Completion[T]:
        completion = await self.caller.complete(messages, schema)
        resolved_key = key or self.key or recording_key(messages, schema)
        envelope = {
            "version": RECORDING_VERSION,
            "key": resolved_key,
            "schema": schema.__name__,
            "schema_json": schema.model_json_schema(),
            "model": completion.model,
            "raw_json": completion.raw_json,
            "prompt_tokens": completion.prompt_tokens,
            "completion_tokens": completion.completion_tokens,
            "latency_ms": completion.latency_ms,
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{resolved_key}.json"
        path.write_text(
            json.dumps(envelope, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return completion


class ReplayCaller:
    """Replay versioned completion envelopes without contacting a provider."""

    def __init__(self, directory: Path, *, key: str | None = None) -> None:
        self.directory = directory
        self.key = key

    async def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        schema: type[T],
        *,
        key: str | None = None,
    ) -> Completion[T]:
        resolved_key = key or self.key or recording_key(messages, schema)
        path = self.directory / f"{resolved_key}.json"
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ModelResponseError("replay", f"cannot read {path}: {exc}") from exc
        if not isinstance(envelope, dict):
            raise ModelResponseError("replay", "recording envelope is malformed")
        if envelope.get("version") != RECORDING_VERSION:
            raise ModelResponseError("replay", "unsupported recording version")
        if envelope.get("key") != resolved_key:
            raise ModelResponseError("replay", "recording key does not match")
        if envelope.get("schema") != schema.__name__:
            raise ModelResponseError("replay", "recording schema does not match")
        if envelope.get("schema_json") != schema.model_json_schema():
            raise ModelResponseError(
                "replay", "recording schema contract does not match"
            )
        raw_json = envelope.get("raw_json")
        model = envelope.get("model")
        if not isinstance(raw_json, str) or not isinstance(model, str):
            raise ModelResponseError("replay", "recording envelope is malformed")
        try:
            parsed = schema.model_validate_json(raw_json)
        except (ValidationError, ValueError) as exc:
            raise ModelResponseError(
                model, f"invalid recorded response: {exc}"
            ) from exc
        return Completion(
            parsed=parsed,
            raw_json=raw_json,
            model=model,
            prompt_tokens=_positive_token_count(envelope, "prompt_tokens"),
            completion_tokens=_positive_token_count(envelope, "completion_tokens"),
            latency_ms=_nonnegative_int(envelope.get("latency_ms")),
        )


def _nonnegative_int(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


__all__ = [
    "RECORDING_VERSION",
    "Completion",
    "LiteLLMCaller",
    "ModelCallError",
    "ModelResponseError",
    "RecordingCaller",
    "ReplayCaller",
    "StructuredCaller",
    "recording_key",
]
