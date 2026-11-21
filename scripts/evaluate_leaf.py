"""Evaluate the eligibility or extractor leaf against reviewed expectations.

Eligibility evaluation replays the one-shot contract at least five times per
case and scores decisions plus reviewed supporting/conflicting evidence
obligations. The six authorized prompt-example cases are excluded from the
primary score and reported in a secondary, contaminated score. Exposed-regression
and blind runs require a freeze record written by a passing development run, so
a candidate cannot be tuned against them. Recordings, safe reports, reason
reviews, and freeze records live only under the ignored ``output/`` directory.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import ValidationError

from job_eligibility import (
    ELIGIBILITY_PROMPT_HASH,
    MAX_EVIDENCE_CHARACTERS,
    PROMPT_EXAMPLES,
    Decision,
    EligibilityResponse,
    build_messages,
    collect_grounding_misses,
    contains_words,
    is_missing_information,
    occurs_in,
    structural_errors,
    validate,
)
from job_extractor import PROMPT_HASH as EXTRACTOR_PROMPT_HASH
from job_extractor import ExtractionResponse, JobResult
from job_extractor import UngroundedEvidenceError as ExtractorGroundingError
from job_extractor import build_messages as extractor_messages
from job_extractor import validate as validate_extraction
from job_pipeline.llm import (
    REASONING_EFFORTS,
    RECORDING_VERSION,
    Completion,
    LiteLLMCaller,
    ModelCallError,
    ModelResponseError,
    ReasoningEffort,
)

ROOT = Path(__file__).resolve().parents[1]
POSTINGS = ROOT / "tests" / "fixtures" / "postings"
DEFAULT_CORPUS = POSTINGS / "opportunity-screening-v2"
DEFAULT_RECORDINGS = ROOT / "output" / "leaf-recordings"
DEFAULT_EXTRACTOR_EXPECTATIONS = ROOT / "output" / "extractor-expectations"
PROJECT_OUTPUT = ROOT / "output"
_DECISIONS: tuple[Decision, ...] = ("eligible", "ineligible", "unknown")
_METADATA_THRESHOLDS = {
    "title": 1.0,
    "work_mode": 0.9,
    "employment_type": 0.9,
    "experience_level": 0.9,
}
_LIST_THRESHOLDS = {
    "responsibilities": (0.9, 0.9),
    "required_qualifications": (0.9, 0.9),
    "preferred_qualifications": (0.0, 0.85),
    "benefits": (0.0, 0.85),
}
_EXTRACTION_EXPECTATION_KEYS = {
    "reviewed",
    "metadata",
    *_LIST_THRESHOLDS,
    "technologies",
    "notes",
}


@dataclass(frozen=True)
class ExtractorEvaluation:
    expected: dict[str, Any]
    actual: JobResult | None
    grounding_failed: bool
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: int
    name: str


def _model_key(model: str) -> str:
    return _sha256(model.encode())


def _require_output_path(path: Path, description: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(PROJECT_OUTPUT.resolve())
    except ValueError as exc:
        raise RuntimeError(
            f"{description} must be under the ignored output directory"
        ) from exc
    return resolved


def _output_child(root: Path, *parts: str | Path) -> Path:
    child = root.joinpath(*parts).resolve()
    try:
        child.relative_to(root.resolve())
        child.relative_to(PROJECT_OUTPUT.resolve())
    except ValueError as exc:
        raise RuntimeError("resolved artifact path escapes output") from exc
    return child


def _directories(root: Path = POSTINGS) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        path.parent for path in root.rglob("content.txt") if path.parent != root
    )


def _reviewed_payload(directory: Path, filename: str) -> dict[str, Any]:
    path = directory / filename
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"missing or invalid expectation: {path}") from exc
    if not isinstance(payload, dict) or payload.get("reviewed") is not True:
        raise RuntimeError(f"expectation is not reviewed: {path}")
    return payload


def _eligibility_payload(
    directory: Path, content: bytes | None = None
) -> dict[str, Any]:
    path = directory / "expected_eligibility.json"
    payload = _reviewed_payload(directory, path.name)
    decision = payload.get("decision")
    must_cite = payload.get("must_cite")
    source = (directory / "content.txt").read_bytes() if content is None else content
    if (
        payload.get("schema_version") != 1
        or payload.get("case_id") != directory.name
        or payload.get("partition") not in _PARTITIONS
        or payload.get("source_content_sha256") != _sha256(source)
        or decision not in _DECISIONS
        or not isinstance(must_cite, list)
        or not must_cite
        or any(not isinstance(value, str) or not value.strip() for value in must_cite)
    ):
        raise RuntimeError(f"invalid eligibility expectation: {path}")
    return payload


def _eligibility_expectation(directory: Path) -> tuple[Decision, list[str]]:
    payload = _eligibility_payload(directory)
    return cast(Decision, payload["decision"]), cast(list[str], payload["must_cite"])


def _expectation(directory: Path) -> Decision:
    return _eligibility_expectation(directory)[0]


def _extractor_directories(directories: list[Path]) -> list[Path]:
    return [
        directory
        for directory in directories
        if _expectation(directory) != "ineligible"
    ]


def _extractor_expectation(
    directory: Path, expectation: Path | None = None
) -> dict[str, Any]:
    path = (
        directory / "expected_extraction.json" if expectation is None else expectation
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"missing or invalid expectation: {path}") from exc
    if not isinstance(payload, dict) or payload.get("reviewed") is not True:
        raise RuntimeError(f"expectation is not reviewed: {path}")
    if (
        set(payload) != _EXTRACTION_EXPECTATION_KEYS
        or not isinstance(payload.get("metadata"), dict)
        or any(not isinstance(payload.get(field), list) for field in _LIST_THRESHOLDS)
        or not isinstance(payload.get("technologies"), list)
    ):
        raise RuntimeError(f"invalid extraction expectation: {path}")
    return payload


def _extractor_recording_key(model: str, source_hash: str) -> str:
    payload = {
        "requested_model": model,
        "prompt_hash": EXTRACTOR_PROMPT_HASH,
        "schema": ExtractionResponse.__name__,
        "schema_json": ExtractionResponse.model_json_schema(),
        "source_content_sha256": source_hash,
        "request": "evaluate",
        "replay": 1,
    }
    return _sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())


async def _extractor_completion(
    text: str,
    model: str,
    record: bool,
    timeout: float,
    recordings: Path,
    key: str,
) -> Completion[ExtractionResponse]:
    path = _output_child(recordings, f"{key}.json")
    expected = {
        "version": RECORDING_VERSION,
        "key": key,
        "requested_model": model,
        "prompt_hash": EXTRACTOR_PROMPT_HASH,
        "schema": ExtractionResponse.__name__,
        "schema_json": ExtractionResponse.model_json_schema(),
        "source_content_sha256": _sha256(text.encode()),
        "request": "evaluate",
        "replay": 1,
    }
    if path.exists() or not record:
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ModelResponseError(
                "replay", "recording is missing or invalid"
            ) from exc
        if not isinstance(envelope, dict) or any(
            envelope.get(name) != value for name, value in expected.items()
        ):
            raise ModelResponseError("replay", "recording is incompatible")
        raw_json = envelope.get("raw_json")
        if not isinstance(raw_json, str):
            raise ModelResponseError("replay", "recording response is invalid")
        try:
            parsed = ExtractionResponse.model_validate_json(raw_json)
        except (ValidationError, ValueError) as exc:
            raise ModelResponseError("replay", "recording response is invalid") from exc
        return Completion(
            parsed=parsed,
            raw_json=raw_json,
            model=str(envelope.get("model", model)),
            prompt_tokens=(
                envelope["prompt_tokens"]
                if isinstance(envelope.get("prompt_tokens"), int)
                else None
            ),
            completion_tokens=(
                envelope["completion_tokens"]
                if isinstance(envelope.get("completion_tokens"), int)
                else None
            ),
            latency_ms=(
                envelope["latency_ms"]
                if isinstance(envelope.get("latency_ms"), int)
                else 0
            ),
        )
    recordings.mkdir(parents=True, exist_ok=True)
    reservation = path.with_name(f".{path.name}.reserved")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(reservation, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    try:
        completion = await LiteLLMCaller(model, timeout=timeout).complete(
            extractor_messages(text), ExtractionResponse
        )
        envelope = {
            **expected,
            "model": completion.model,
            "raw_json": completion.raw_json,
            "prompt_tokens": completion.prompt_tokens,
            "completion_tokens": completion.completion_tokens,
            "latency_ms": completion.latency_ms,
        }
        file_descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as stream:
            json.dump(envelope, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return completion
    finally:
        temporary.unlink(missing_ok=True)
        reservation.unlink(missing_ok=True)


async def _evaluate_extractor_one(
    directory: Path,
    expected: dict[str, Any],
    model: str,
    record: bool,
    timeout: float,
    recordings: Path,
) -> ExtractorEvaluation:
    text = (directory / "content.txt").read_text(encoding="utf-8")
    key = _extractor_recording_key(model, _sha256(text.encode()))
    completion = await _extractor_completion(
        text, model, record, timeout, recordings, key
    )
    try:
        result = validate_extraction(completion.parsed, text)
    except ExtractorGroundingError:
        result = None
    return ExtractorEvaluation(
        expected=expected,
        actual=result,
        grounding_failed=result is None,
        prompt_tokens=completion.prompt_tokens,
        completion_tokens=completion.completion_tokens,
        latency_ms=completion.latency_ms,
        name=directory.name,
    )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _validate_expectation_tree(
    root: Path, corpus: Path, directories: list[Path], filename: str
) -> None:
    expected = {
        _output_child(
            root,
            directory.resolve().relative_to(corpus.resolve()),
            filename,
        )
        for directory in directories
    }
    actual_files = {path.resolve() for path in root.rglob("*") if path.is_file()}
    actual_directories = {path.resolve() for path in root.rglob("*") if path.is_dir()}
    expected_directories = {path.parent for path in expected} | {root.resolve()}
    if actual_files != expected or not actual_directories <= expected_directories:
        raise RuntimeError(
            "expectation directory does not exactly match selected corpus"
        )


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0


def _usage(results: list[Any], input_price: float, output_price: float) -> str:
    prompt_tokens = sum(result.prompt_tokens or 0 for result in results)
    completion_tokens = sum(result.completion_tokens or 0 for result in results)
    cost = (
        prompt_tokens * input_price / 1_000_000
        + completion_tokens * output_price / 1_000_000
    )
    return (
        f"prompt_tokens={prompt_tokens} completion_tokens={completion_tokens} "
        f"estimated_cost={cost:.6f}"
    )


def _latency(results: list[Any]) -> str:
    total = sum(result.latency_ms for result in results)
    per_posting = total / len(results) if results else 0.0
    return f"latency_ms_total={total} latency_ms_per_posting={per_posting:.1f}"


def _key(value: Any) -> str:
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, default=str).casefold()
    return str(value).casefold().strip()


def _fact_value(result: JobResult, field: str) -> dict[str, Any]:
    dumped = getattr(result, field).model_dump(mode="json")
    return {"state": dumped["state"], "value": dumped["value"]}


def _actual_list(result: JobResult, field: str) -> set[str]:
    values = getattr(result, field).value or []
    return {_key(value.text if hasattr(value, "text") else value) for value in values}


def _expected_technologies(values: list[Any]) -> set[tuple[str, str]]:
    return {
        (_key(value.get("name")), _key(value.get("status")))
        if isinstance(value, dict)
        else (_key(value), "")
        for value in values
    }


def _actual_technologies(result: JobResult) -> set[tuple[str, str]]:
    return {
        (_key(item.canonical or item.mentions[0].mention), _key(item.status))
        for item in result.technologies_normalized
    }


def _rates(expected: set[Any], actual: set[Any]) -> tuple[int, int, int]:
    return len(expected & actual), len(actual), len(expected)


def _print_extractor_report(
    results: list[ExtractorEvaluation], input_price: float, output_price: float
) -> bool:
    metadata: dict[str, list[int]] = {}
    lists = {field: [0, 0, 0] for field in _LIST_THRESHOLDS}
    technology = [0, 0, 0]
    grounding_failures = failed_fields = 0
    for evaluation in results:
        grounding_failures += int(evaluation.grounding_failed)
        if evaluation.actual is None:
            print(f"grounding_failed {evaluation.name}")
            continue
        actual = evaluation.actual
        failed_fields += sum(
            getattr(actual, field).state == "failed"
            for field in ExtractionResponse.model_fields
        )
        for field, expected in evaluation.expected["metadata"].items():
            counts = metadata.setdefault(field, [0, 0])
            counts[1] += 1
            counts[0] += int(_key(_fact_value(actual, field)) == _key(expected))
        for field in _LIST_THRESHOLDS:
            values = {_key(value) for value in evaluation.expected[field]}
            matched, predicted, wanted = _rates(values, _actual_list(actual, field))
            lists[field][0] += matched
            lists[field][1] += predicted
            lists[field][2] += wanted
        matched, predicted, wanted = _rates(
            _expected_technologies(evaluation.expected["technologies"]),
            _actual_technologies(actual),
        )
        technology[0] += matched
        technology[1] += predicted
        technology[2] += wanted
    accepted = grounding_failures == 0
    for field, (matches, total) in sorted(metadata.items()):
        exact = _ratio(matches, total)
        print(f"metadata {field} exact={exact:.3f} ({matches}/{total})")
        if field in _METADATA_THRESHOLDS:
            accepted = accepted and exact >= _METADATA_THRESHOLDS[field]
    for field, (matches, predicted, expected) in lists.items():
        precision, recall = _ratio(matches, predicted), _ratio(matches, expected)
        print(f"list {field} precision={precision:.3f} recall={recall:.3f}")
        minimum_precision, minimum_recall = _LIST_THRESHOLDS[field]
        accepted = (
            accepted and precision >= minimum_precision and recall >= minimum_recall
        )
    precision, recall = (
        _ratio(technology[0], technology[1]),
        _ratio(technology[0], technology[2]),
    )
    print(f"technologies precision={precision:.3f} recall={recall:.3f}")
    accepted = accepted and precision >= 0.9 and recall >= 0.9
    usage = _usage(results, input_price, output_price)
    print(
        f"failed_fields={failed_fields} grounding_failures={grounding_failures} "
        f"{usage} {_latency(results)}"
    )
    return accepted


_OBLIGATION_KEYS = {"obligation_id", "role", "critical", "excerpt_alternatives"}
_EVIDENCE_KEYS = {
    "schema_version",
    "case_id",
    "reviewed",
    "review_status",
    "partition",
    "source_content_sha256",
    "decision",
    "prompt_example",
    "missing_information",
    "obligations",
    "conflict_pairs",
}
_ROLES = ("supporting", "conflicting", "either")
_BUCKETS = ("supporting", "conflicting")
CallStatus = Literal[
    "grounded", "inconsistent", "ungrounded", "invalid_response", "error"
]
GateProfile = Literal["development", "blind", "report"]
# A blind corpus lives in its own directory (pass it with --corpus); its cases
# must carry operator-reviewed evidence obligations written before any call.
_PARTITIONS = frozenset({"development", "exposed_regression", "blind"})
RECORDING_VERSION_ONE_SHOT = 3
FREEZE_VERSION = 1
# Grounding must reach this share of calls, and critical evidence recall is
# measured over grounded calls only: a rejected excerpt is retried in production
# and never stored, so it costs a retry rather than a wrong decision.
MIN_GROUNDING_RATE = 0.99


@dataclass(frozen=True)
class Obligation:
    obligation_id: str
    role: Literal["supporting", "conflicting", "either"]
    critical: bool
    alternatives: tuple[str, ...]


@dataclass(frozen=True)
class EligibilityCase:
    case_id: str
    directory: Path
    relative_path: Path
    text: str
    source_hash: str
    partition: str
    expected_decision: Decision
    obligations: tuple[Obligation, ...]
    conflict_pairs: tuple[tuple[str, str], ...]
    missing_information: bool
    prompt_example: str | None
    obligations_source: Literal["reviewed", "pending_review", "must_cite"]

    @property
    def safety_critical(self) -> bool:
        """A false eligible here would retain an excluded or unresolved role."""
        return self.expected_decision != "eligible" or bool(self.conflict_pairs)


@dataclass(frozen=True)
class CallEvaluation:
    case: EligibilityCase
    replay: int
    status: CallStatus
    decision: Decision | None
    reason: str | None
    satisfied: frozenset[str]
    pairs_satisfied: bool
    missing_information: bool
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: int

    @property
    def decision_correct(self) -> bool:
        return (
            self.status == "grounded" and self.decision == self.case.expected_decision
        )

    @property
    def evidence_agreement(self) -> bool:
        """Every obligation is met in its role, and conflict pairs split buckets."""
        if self.status != "grounded":
            return False
        if self.case.missing_information:
            return self.missing_information
        return self.pairs_satisfied and all(
            obligation.obligation_id in self.satisfied
            for obligation in self.case.obligations
        )

    @property
    def key(self) -> str:
        return f"{self.case.case_id}#{self.replay}"


def _alternatives(value: object, content: str, path: Path) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise RuntimeError(f"invalid evidence obligation: {path}")
    alternatives: list[str] = []
    for alternative in value:
        if (
            not isinstance(alternative, str)
            or not alternative.strip()
            or len(alternative) > MAX_EVIDENCE_CHARACTERS
            or not occurs_in(alternative, content)
        ):
            raise RuntimeError(f"evidence obligation is not grounded: {path}")
        alternatives.append(alternative)
    return tuple(alternatives)


def _parse_obligations(
    payload: dict[str, Any], case_id: str, content: str, path: Path
) -> tuple[tuple[Obligation, ...], tuple[tuple[str, str], ...]]:
    values = payload.get("obligations")
    if not isinstance(values, list):
        raise RuntimeError(f"invalid evidence obligations: {path}")
    obligations: list[Obligation] = []
    for index, value in enumerate(values, start=1):
        if (
            not isinstance(value, dict)
            or set(value) != _OBLIGATION_KEYS
            or value.get("obligation_id") != f"{case_id}-E{index:02d}"
            or value.get("role") not in _ROLES
            or not isinstance(value.get("critical"), bool)
        ):
            raise RuntimeError(f"invalid evidence obligation: {path}")
        obligations.append(
            Obligation(
                obligation_id=value["obligation_id"],
                role=value["role"],
                critical=value["critical"],
                alternatives=_alternatives(
                    value.get("excerpt_alternatives"), content, path
                ),
            )
        )
    roles = {obligation.obligation_id: obligation.role for obligation in obligations}
    raw_pairs = payload.get("conflict_pairs")
    if not isinstance(raw_pairs, list):
        raise RuntimeError(f"invalid conflict pairs: {path}")
    pairs: list[tuple[str, str]] = []
    for pair in raw_pairs:
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or pair[0] == pair[1]
            or any(roles.get(item) != "either" for item in pair)
        ):
            raise RuntimeError(f"invalid conflict pairs: {path}")
        pairs.append((pair[0], pair[1]))
    return tuple(obligations), tuple(pairs)


def _prompt_example_cases(corpus: Path) -> dict[str, str]:
    """Return case → pattern for corpus cases that back prompt examples.

    A manifest with ``source: synthetic`` declares that no corpus case backs an
    example, so every case is scored as uncontaminated. A ``corpus`` source must
    map one case to each current example pattern; those cases are excluded from
    the primary score.
    """
    path = corpus / "manifest.json"
    if not path.is_file():
        return {}
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid corpus manifest: {path}") from exc
    section = manifest.get("prompt_examples") if isinstance(manifest, dict) else None
    if section is None:
        return {}
    patterns = section.get("patterns") if isinstance(section, dict) else None
    source = section.get("source") if isinstance(section, dict) else None
    if not isinstance(patterns, dict) or source not in {"synthetic", "corpus"}:
        raise RuntimeError("corpus prompt examples do not match the current contract")
    if source == "synthetic":
        if patterns:
            raise RuntimeError("synthetic prompt examples cannot name corpus cases")
        return {}
    if set(patterns.values()) != {
        example.pattern for example in PROMPT_EXAMPLES
    } or len(patterns) != len(PROMPT_EXAMPLES):
        raise RuntimeError("corpus prompt examples do not match the current contract")
    return {str(case_id): str(pattern) for case_id, pattern in patterns.items()}


def _load_case(
    directory: Path, relative_path: Path, examples: dict[str, str]
) -> EligibilityCase:
    content_bytes = (directory / "content.txt").read_bytes()
    content = content_bytes.decode("utf-8")
    expected = _eligibility_payload(directory, content_bytes)
    decision = cast(Decision, expected["decision"])
    source_hash = _sha256(content_bytes)
    path = directory / "expected_evidence.json"
    example = examples.get(directory.name)
    if not path.exists():
        if example is not None:
            raise RuntimeError(f"prompt example lacks reviewed obligations: {path}")
        if expected["partition"] == "blind":
            raise RuntimeError(f"blind case lacks reviewed obligations: {path}")
        must_cite = tuple(cast(list[str], expected["must_cite"]))
        for quote in must_cite:
            if not occurs_in(quote, content):
                raise RuntimeError(f"must_cite quote is not grounded: {directory}")
        obligation = Obligation(f"{directory.name}-E01", "either", True, must_cite)
        return EligibilityCase(
            directory.name,
            directory,
            relative_path,
            content,
            source_hash,
            str(expected["partition"]),
            decision,
            (obligation,),
            (),
            False,
            None,
            "must_cite",
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"missing or invalid evidence obligations: {path}") from exc
    reviewed = payload.get("reviewed") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or set(payload) != _EVIDENCE_KEYS
        or payload.get("schema_version") != 1
        or payload.get("case_id") != directory.name
        or payload.get("partition") != expected["partition"]
        or payload.get("source_content_sha256") != source_hash
        or payload.get("decision") != decision
        or not isinstance(reviewed, bool)
        or payload.get("review_status")
        != ("reviewed" if reviewed else "pending_operator_review")
        or payload.get("prompt_example") != example
        or not isinstance(payload.get("missing_information"), bool)
    ):
        raise RuntimeError(f"invalid evidence obligations: {path}")
    obligations, pairs = _parse_obligations(payload, directory.name, content, path)
    missing = bool(payload["missing_information"])
    if missing != (not obligations) or (missing and decision != "unknown"):
        raise RuntimeError(f"invalid missing-information obligations: {path}")
    cited = {quote for item in obligations for quote in item.alternatives}
    if not missing and not set(cast(list[str], expected["must_cite"])) <= cited:
        raise RuntimeError(f"obligations drop a reviewed must_cite quote: {path}")
    if pairs and decision != "unknown":
        raise RuntimeError(f"conflict pairs require an unknown decision: {path}")
    return EligibilityCase(
        directory.name,
        directory,
        relative_path,
        content,
        source_hash,
        str(expected["partition"]),
        decision,
        obligations,
        pairs,
        missing,
        example,
        "reviewed" if reviewed else "pending_review",
    )


def _satisfaction(
    case: EligibilityCase, supporting: Sequence[str], conflicting: Sequence[str]
) -> tuple[frozenset[str], bool]:
    """Which obligations a response meets, and whether conflict pairs split."""
    cited = {"supporting": supporting, "conflicting": conflicting}
    buckets: dict[str, set[str]] = {}
    for obligation in case.obligations:
        found = {
            bucket
            for bucket in _BUCKETS
            for alternative in obligation.alternatives
            for value in cited[bucket]
            if contains_words(value, alternative)
        }
        allowed = set(_BUCKETS) if obligation.role == "either" else {obligation.role}
        buckets[obligation.obligation_id] = found & allowed
    satisfied = frozenset(name for name, found in buckets.items() if found)
    pairs = all(
        any(
            first != second
            for first in buckets.get(left, set())
            for second in buckets.get(right, set())
        )
        for left, right in case.conflict_pairs
    )
    return satisfied, pairs


def _recording_key(
    model: str,
    case: EligibilityCase,
    replay: int,
    reasoning_effort: ReasoningEffort | None = None,
) -> str:
    payload: dict[str, object] = {
        "model": model,
        "eligibility_prompt_hash": ELIGIBILITY_PROMPT_HASH,
        "case_id": case.case_id,
        "source_hash": case.source_hash,
        "replay": replay,
    }
    # Added only when set, so recordings made without an effort keep their keys.
    if reasoning_effort is not None:
        payload["reasoning_effort"] = reasoning_effort
    return _sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())


def _recording_path(
    directory: Path,
    model: str,
    case: EligibilityCase,
    replay: int,
    reasoning_effort: ReasoningEffort | None = None,
) -> Path:
    return _output_child(
        directory,
        _model_key(model),
        case.relative_path,
        f"{_recording_key(model, case, replay, reasoning_effort)}.json",
    )


def _recording_contract(
    path: Path,
    model: str,
    case: EligibilityCase,
    replay: int,
    reasoning_effort: ReasoningEffort | None = None,
) -> dict[str, object]:
    contract: dict[str, object] = {
        "recording_version": RECORDING_VERSION_ONE_SHOT,
        "key": path.stem,
        "requested_model": model,
        "eligibility_prompt_hash": ELIGIBILITY_PROMPT_HASH,
        "case_id": case.case_id,
        "source_content_sha256": case.source_hash,
        "replay": replay,
    }
    if reasoning_effort is not None:
        contract["reasoning_effort"] = reasoning_effort
    return contract


@dataclass(frozen=True)
class _Recorded:
    """One recorded call: a parsed response, or a schema-invalid response."""

    parsed: EligibilityResponse | None
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: int


def _load_recording(
    path: Path,
    model: str,
    case: EligibilityCase,
    replay: int,
    reasoning_effort: ReasoningEffort | None = None,
) -> _Recorded:
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"missing or invalid recording for {case.case_id} replay {replay}"
        ) from exc
    contract = _recording_contract(path, model, case, replay, reasoning_effort)
    if (
        not isinstance(envelope, dict)
        or envelope.get("reasoning_effort") != reasoning_effort
        or any(envelope.get(key) != value for key, value in contract.items())
    ):
        raise RuntimeError(f"incompatible recording for {case.case_id} replay {replay}")
    raw_json = envelope.get("raw_json")
    status = envelope.get("status")
    parsed: EligibilityResponse | None = None
    if status == "parsed" and isinstance(raw_json, str):
        try:
            parsed = EligibilityResponse.model_validate_json(raw_json)
        except (ValidationError, ValueError) as exc:
            raise RuntimeError(
                f"invalid recorded response for {case.case_id} replay {replay}"
            ) from exc
    elif not (status == "invalid_response" and raw_json is None):
        raise RuntimeError(
            f"invalid recorded response for {case.case_id} replay {replay}"
        )

    def count(name: str) -> int | None:
        value = envelope.get(name)
        return value if isinstance(value, int) and value >= 0 else None

    return _Recorded(
        parsed=parsed,
        model=str(envelope.get("response_model", model)),
        prompt_tokens=count("prompt_tokens"),
        completion_tokens=count("completion_tokens"),
        latency_ms=count("latency_ms") or 0,
    )


def _write_envelope(path: Path, envelope: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    file_descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as stream:
            json.dump(envelope, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


async def _eligibility_completion(
    case: EligibilityCase,
    replay: int,
    model: str,
    record: bool,
    timeout: float,
    directory: Path,
    reasoning_effort: ReasoningEffort | None = None,
) -> _Recorded:
    """Replay a recorded call, or record a missing one when ``--record`` is set.

    Schema-invalid responses are recorded too, so a rerun scores the same call
    rather than silently re-sampling it; transport failures are not recorded.
    """
    directory = _require_output_path(directory, "recording output")
    path = _recording_path(directory, model, case, replay, reasoning_effort)
    if path.exists() or not record:
        return _load_recording(path, model, case, replay, reasoning_effort)
    path.parent.mkdir(parents=True, exist_ok=True)
    reservation = path.with_name(f".{path.name}.reserved")
    descriptor = os.open(reservation, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    contract = _recording_contract(path, model, case, replay, reasoning_effort)
    try:
        started = time.monotonic()
        try:
            completion = await LiteLLMCaller(
                model, timeout=timeout, reasoning_effort=reasoning_effort
            ).complete(build_messages(case.text), EligibilityResponse)
        except ModelResponseError:
            latency = round((time.monotonic() - started) * 1000)
            _write_envelope(
                path,
                {
                    **contract,
                    "status": "invalid_response",
                    "raw_json": None,
                    "latency_ms": latency,
                },
            )
            return _Recorded(None, model, None, None, latency)
        _write_envelope(
            path,
            {
                **contract,
                "status": "parsed",
                "raw_json": completion.raw_json,
                "response_model": completion.model,
                "prompt_tokens": completion.prompt_tokens,
                "completion_tokens": completion.completion_tokens,
                "latency_ms": completion.latency_ms,
            },
        )
        return _Recorded(
            completion.parsed,
            completion.model,
            completion.prompt_tokens,
            completion.completion_tokens,
            completion.latency_ms,
        )
    finally:
        reservation.unlink(missing_ok=True)


def _evaluate_response(
    case: EligibilityCase, replay: int, recorded: _Recorded
) -> CallEvaluation:
    response = recorded.parsed
    if response is None:
        return CallEvaluation(
            case,
            replay,
            "invalid_response",
            None,
            None,
            frozenset(),
            False,
            False,
            recorded.prompt_tokens,
            recorded.completion_tokens,
            recorded.latency_ms,
        )
    if structural_errors(response):
        status: CallStatus = "inconsistent"
    elif collect_grounding_misses(response, case.text):
        status = "ungrounded"
    else:
        status = "grounded"
        validate(response, case.text)
    satisfied, pairs = _satisfaction(
        case, response.supporting_evidence, response.conflicting_evidence
    )
    return CallEvaluation(
        case,
        replay,
        status,
        response.decision if status == "grounded" else None,
        response.reason,
        satisfied,
        pairs,
        is_missing_information(response),
        recorded.prompt_tokens,
        recorded.completion_tokens,
        recorded.latency_ms,
    )


async def _evaluate_one(
    case: EligibilityCase, replay: int, args: argparse.Namespace
) -> CallEvaluation:
    try:
        recorded = await _eligibility_completion(
            case,
            replay,
            args.model,
            args.record,
            args.timeout,
            args.recordings_dir,
            args.reasoning_effort,
        )
    except ModelCallError:
        return CallEvaluation(
            case, replay, "error", None, None, frozenset(), False, False, None, None, 0
        )
    return _evaluate_response(case, replay, recorded)


@dataclass(frozen=True)
class ScopeMetrics:
    calls: int
    schema_valid: int
    consistent: int
    grounded: int
    confusion: dict[str, Counter[str]]
    macro_f1: float
    critical_hits: int
    critical_total: int
    exact_agreement: int
    false_eligible: int
    wrongful_ineligible: int
    unanimous_cases: int
    case_count: int
    unstable_safety_cases: tuple[str, ...]
    divergent: tuple[CallEvaluation, ...]

    def rate(self, value: int, total: int | None = None) -> float:
        return _ratio(value, self.calls if total is None else total)


def _macro_f1(results: Sequence[CallEvaluation]) -> float:
    scores = []
    for decision in _DECISIONS:
        tp = sum(
            r.case.expected_decision == decision and r.decision == decision
            for r in results
        )
        fp = sum(
            r.case.expected_decision != decision and r.decision == decision
            for r in results
        )
        fn = sum(
            r.case.expected_decision == decision and r.decision != decision
            for r in results
        )
        if tp + fp + fn == 0:
            continue
        precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
        scores.append(
            2 * precision * recall / (precision + recall) if precision + recall else 0.0
        )
    return sum(scores) / len(scores) if scores else 1.0


def _scope_metrics(results: Sequence[CallEvaluation]) -> ScopeMetrics:
    confusion: dict[str, Counter[str]] = {d: Counter() for d in _DECISIONS}
    for result in results:
        actual = result.decision if result.status == "grounded" else "error"
        confusion[result.case.expected_decision][str(actual)] += 1
    by_case: dict[str, list[CallEvaluation]] = defaultdict(list)
    for result in results:
        by_case[result.case.case_id].append(result)
    unanimous = {
        case_id
        for case_id, calls in by_case.items()
        if all(call.status == "grounded" for call in calls)
        and len({call.decision for call in calls}) == 1
    }
    unstable_safety = tuple(
        sorted(
            case_id
            for case_id, calls in by_case.items()
            if calls[0].case.safety_critical and case_id not in unanimous
        )
    )
    divergent = tuple(
        result
        for result in results
        if result.case.case_id not in unanimous
        or not result.decision_correct
        or not result.evidence_agreement
    )
    # Recall is over accepted (grounded) calls; ungrounded calls fail grounding.
    critical = [
        (result, obligation)
        for result in results
        if result.status == "grounded"
        for obligation in result.case.obligations
        if obligation.critical
    ]
    return ScopeMetrics(
        calls=len(results),
        schema_valid=sum(
            r.status not in {"invalid_response", "error"} for r in results
        ),
        consistent=sum(r.status in {"grounded", "ungrounded"} for r in results),
        grounded=sum(r.status == "grounded" for r in results),
        confusion=confusion,
        macro_f1=_macro_f1(results),
        critical_hits=sum(
            obligation.obligation_id in result.satisfied
            for result, obligation in critical
        ),
        critical_total=len(critical),
        exact_agreement=sum(
            r.decision_correct and r.evidence_agreement for r in results
        ),
        false_eligible=sum(
            r.case.expected_decision != "eligible" and r.decision == "eligible"
            for r in results
        ),
        wrongful_ineligible=sum(
            r.case.expected_decision == "eligible" and r.decision == "ineligible"
            for r in results
        ),
        unanimous_cases=len(unanimous),
        case_count=len(by_case),
        unstable_safety_cases=unstable_safety,
        divergent=divergent,
    )


def _scope_lines(name: str, metrics: ScopeMetrics) -> list[str]:
    lines = [
        f"[{name}] cases={metrics.case_count} calls={metrics.calls}",
        f"schema_valid={metrics.rate(metrics.schema_valid):.3f} "
        f"({metrics.schema_valid}/{metrics.calls})",
        f"structural_consistency={metrics.rate(metrics.consistent):.3f} "
        f"({metrics.consistent}/{metrics.calls})",
        f"grounding={metrics.rate(metrics.grounded):.3f} "
        f"({metrics.grounded}/{metrics.calls})",
        f"decision_macro_f1={metrics.macro_f1:.3f}",
        f"critical_evidence_recall="
        f"{metrics.rate(metrics.critical_hits, metrics.critical_total):.3f} "
        f"({metrics.critical_hits}/{metrics.critical_total})",
        f"decision_evidence_role_agreement={metrics.rate(metrics.exact_agreement):.3f} "
        f"({metrics.exact_agreement}/{metrics.calls})",
        f"false_eligible={metrics.false_eligible} "
        f"wrongful_ineligible={metrics.wrongful_ineligible}",
        "unanimous_cases="
        f"{metrics.rate(metrics.unanimous_cases, metrics.case_count):.3f}"
        f" ({metrics.unanimous_cases}/{metrics.case_count}) "
        "unstable_safety_cases=" + (",".join(metrics.unstable_safety_cases) or "none"),
        "expected\\actual eligible ineligible unknown error",
    ]
    for expected in _DECISIONS:
        lines.append(
            f"{expected} "
            + " ".join(
                str(metrics.confusion[expected][actual])
                for actual in (*_DECISIONS, "error")
            )
        )
    return lines


def _automated_gates(metrics: ScopeMetrics, profile: GateProfile) -> list[str]:
    """Return the names of failed automated gates for *profile*."""
    if profile == "report":
        return []
    failures: list[str] = []
    checks = {
        "grounding": metrics.calls > 0
        and metrics.rate(metrics.grounded) >= MIN_GROUNDING_RATE,
        "false_eligible": metrics.false_eligible == 0,
        "wrongful_ineligible": metrics.wrongful_ineligible == 0,
        "critical_evidence_recall": metrics.critical_hits == metrics.critical_total,
        "safety_case_stability": not metrics.unstable_safety_cases,
    }
    if profile == "development":
        checks |= {
            "schema_valid": metrics.schema_valid == metrics.calls,
            "structural_consistency": metrics.consistent == metrics.calls,
            "decision_macro_f1": metrics.macro_f1 >= 0.9,
            "decision_evidence_role_agreement": metrics.rate(metrics.exact_agreement)
            >= 0.9,
            "unanimous_cases": metrics.rate(metrics.unanimous_cases, metrics.case_count)
            >= 0.9,
        }
    else:
        checks["decision_agreement"] = (
            metrics.rate(
                sum(metrics.confusion[decision][decision] for decision in _DECISIONS)
            )
            >= 0.9
        )
    failures.extend(name for name, passed in checks.items() if not passed)
    return failures


def _dispositions(
    path: Path | None,
    model: str,
    reasoning_effort: ReasoningEffort | None = None,
) -> dict[str, Literal["accepted", "rejected"]]:
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("reason dispositions are missing or malformed") from exc
    values = payload.get("dispositions") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or payload.get("model") != model
        or payload.get("reasoning_effort") != reasoning_effort
        or payload.get("eligibility_prompt_hash") != ELIGIBILITY_PROMPT_HASH
        or not isinstance(values, dict)
    ):
        raise RuntimeError("reason dispositions do not match the evaluated contract")
    parsed: dict[str, Literal["accepted", "rejected"]] = {}
    for key, value in values.items():
        disposition = value.get("disposition") if isinstance(value, dict) else None
        if disposition not in {"accepted", "rejected"}:
            raise RuntimeError("reason dispositions must be accepted or rejected")
        parsed[str(key)] = disposition
    return parsed


def _reason_review_payload(
    divergent: Sequence[CallEvaluation],
    model: str,
    reasoning_effort: ReasoningEffort | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "eligibility_prompt_hash": ELIGIBILITY_PROMPT_HASH,
        "instructions": (
            "Review each divergent reason against its posting and set disposition "
            "to accepted or rejected; pass this file as --reason-dispositions."
        ),
        "dispositions": {
            result.key: {
                "expected_decision": result.case.expected_decision,
                "status": result.status,
                "decision": result.decision,
                "reason": result.reason,
                "disposition": "pending",
            }
            for result in divergent
        },
    }


def _exclusive_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(payload)
    except FileExistsError as exc:
        raise RuntimeError(f"refusing to overwrite {path.name}") from exc


def _freeze_payload(
    model: str,
    replays: int,
    report_sha256: str,
    reasoning_effort: ReasoningEffort | None = None,
) -> dict[str, object]:
    return {
        "freeze_version": FREEZE_VERSION,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "eligibility_prompt_hash": ELIGIBILITY_PROMPT_HASH,
        "replays": replays,
        "development_report_sha256": report_sha256,
    }


def _require_freeze(
    path: Path | None, model: str, reasoning_effort: ReasoningEffort | None = None
) -> None:
    """Refuse exposed-regression or blind runs of an unfrozen candidate."""
    if path is None:
        raise RuntimeError(
            "the candidate must be frozen by a passing development run before "
            "exposed-regression or blind evaluation; pass --freeze-record"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("freeze record is missing or malformed") from exc
    if not isinstance(payload, dict) or any(
        payload.get(key) != value
        for key, value in {
            "freeze_version": FREEZE_VERSION,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "eligibility_prompt_hash": ELIGIBILITY_PROMPT_HASH,
        }.items()
    ):
        raise RuntimeError("freeze record does not match the current candidate")


def _eligibility_report(
    results: Sequence[CallEvaluation],
    *,
    model: str | None = None,
    reasoning_effort: ReasoningEffort | None = None,
    profile: GateProfile,
    dispositions: dict[str, Literal["accepted", "rejected"]],
    input_price: float,
    output_price: float,
) -> tuple[str, bool, tuple[CallEvaluation, ...]]:
    cases = {result.case.case_id: result.case for result in results}
    examples = sorted(
        case_id for case_id, case in cases.items() if case.prompt_example is not None
    )
    pending = sorted(
        case_id
        for case_id, case in cases.items()
        if case.obligations_source == "pending_review"
    )
    primary_results = [r for r in results if r.case.prompt_example is None]
    primary = _scope_metrics(primary_results)
    lines = [
        f"contract prompt_hash={ELIGIBILITY_PROMPT_HASH} gate_profile={profile}",
        *(
            [f"candidate model={model} reasoning_effort={reasoning_effort or 'none'}"]
            if model is not None
            else []
        ),
        "prompt_example_cases_excluded_from_primary=" + (",".join(examples) or "none"),
        "obligations_pending_review=" + (",".join(pending) or "none"),
        *_scope_lines("primary", primary),
    ]
    if examples:
        lines.extend(
            _scope_lines("secondary_all_including_examples", _scope_metrics(results))
        )
    grouped: dict[str, Counter[str]] = defaultdict(Counter)
    for result in sorted(results, key=lambda item: (item.case.case_id, item.replay)):
        actual = result.decision if result.status == "grounded" else result.status
        role = "evidence_ok" if result.evidence_agreement else "evidence_miss"
        grouped[result.case.case_id][f"{actual}/{role}"] += 1
    for case_id in sorted(grouped):
        case = cases[case_id]
        distribution = " ".join(
            f"{outcome}={count}" for outcome, count in sorted(grouped[case_id].items())
        )
        lines.append(
            f"case_distribution case={case_id} expected={case.expected_decision} "
            f"obligations={case.obligations_source} {distribution}"
        )
    lines.append(
        _usage(list(results), input_price, output_price) + " " + _latency(list(results))
    )
    failures = _automated_gates(primary, profile)
    if profile != "report" and pending:
        failures.append("evidence_obligations_reviewed")
    divergent = primary.divergent
    unreviewed = [
        result.key for result in divergent if dispositions.get(result.key) != "accepted"
    ]
    lines.append(
        f"manual_reason_review divergent_calls={len(divergent)} "
        f"accepted={len(divergent) - len(unreviewed)} "
        f"pending_or_rejected={len(unreviewed)}"
    )
    if profile != "report" and unreviewed:
        failures.append("manual_reason_review")
    accepted = profile != "report" and not failures
    lines.append(
        "gates="
        + ("report_only" if profile == "report" else "pass" if accepted else "fail")
        + (f" failed={','.join(failures)}" if failures else "")
    )
    return "\n".join(lines) + "\n", accepted, divergent


async def _run_eligibility(args: argparse.Namespace, recordings_dir: Path) -> int:
    profile: GateProfile = args.gate_profile or (
        "development"
        if args.partition == "development"
        else "blind"
        if args.partition == "blind"
        else "report"
    )
    if args.partition != "development" or profile == "blind":
        _require_freeze(args.freeze_record, args.model, args.reasoning_effort)
    if args.write_freeze is not None:
        if profile != "development":
            raise RuntimeError("--write-freeze requires a development gate run")
        args.write_freeze = _require_output_path(args.write_freeze, "freeze record")
        if args.write_freeze.exists():
            raise RuntimeError("freeze record already exists")
    review_out = None
    if args.reason_review_out is not None:
        review_out = _require_output_path(args.reason_review_out, "reason review")
        if review_out.exists():
            raise RuntimeError("reason review output already exists")
    dispositions = _dispositions(
        args.reason_dispositions, args.model, args.reasoning_effort
    )
    corpus = args.corpus.resolve()
    directories = _directories(corpus)
    if not directories:
        raise RuntimeError(f"posting corpus is unavailable: {args.corpus}")
    if args.partition != "all":
        directories = [
            directory
            for directory in directories
            if _eligibility_payload(directory)["partition"] == args.partition
        ]
    if not directories:
        raise RuntimeError("selected partition contains no cases")
    examples = _prompt_example_cases(corpus)
    cases = [
        _load_case(directory, directory.resolve().relative_to(corpus), examples)
        for directory in directories
    ]
    if len({case.case_id for case in cases}) != len(cases):
        raise RuntimeError("duplicate case ids")
    args.recordings_dir = recordings_dir
    results = [
        await _evaluate_one(case, replay, args)
        for case in cases
        for replay in range(1, args.replays + 1)
    ]
    report, accepted, divergent = _eligibility_report(
        results,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        profile=profile,
        dispositions=dispositions,
        input_price=args.price_per_million[0],
        output_price=args.price_per_million[1],
    )
    print(report, end="")
    if args.safe_report is not None:
        _exclusive_write(args.safe_report, report)
    if review_out is not None:
        _exclusive_write(
            review_out,
            json.dumps(
                _reason_review_payload(divergent, args.model, args.reasoning_effort),
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
        )
    if accepted and args.write_freeze is not None:
        report_hash = _sha256(report.encode())
        _exclusive_write(
            args.write_freeze,
            json.dumps(
                _freeze_payload(
                    args.model, args.replays, report_hash, args.reasoning_effort
                ),
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
    return int(not accepted)


async def _run(args: argparse.Namespace) -> int:
    recordings_dir = _require_output_path(args.recordings_dir, "recording output")
    if args.safe_report is not None:
        args.safe_report = _require_output_path(args.safe_report, "safe report")
        if args.safe_report.exists():
            raise RuntimeError("safe report already exists")
    if args.leaf == "eligibility":
        return await _run_eligibility(args, recordings_dir)
    if args.reasoning_effort is not None:
        raise RuntimeError("--reasoning-effort applies only to --leaf eligibility")
    directories = _directories(args.corpus)
    if not directories:
        raise RuntimeError(f"posting corpus is unavailable: {args.corpus}")
    if args.partition != "all":
        directories = [
            directory
            for directory in directories
            if _eligibility_payload(directory)["partition"] == args.partition
        ]
    selected = _extractor_directories(directories)
    if not selected:
        raise RuntimeError("selected partition contains no extractor cases")
    expectation_root = _require_output_path(
        args.extractor_expectations, "extractor expectations"
    )
    _validate_expectation_tree(
        expectation_root, args.corpus, selected, "expected_extraction.json"
    )
    prepared = [
        (
            directory,
            _extractor_expectation(
                directory,
                _output_child(
                    expectation_root,
                    directory.resolve().relative_to(args.corpus.resolve()),
                    "expected_extraction.json",
                ),
            ),
        )
        for directory in selected
    ]
    extractor_results = [
        await _evaluate_extractor_one(
            directory,
            expectation,
            args.model,
            args.record,
            args.timeout,
            _output_child(
                recordings_dir,
                "extractor",
                _model_key(args.model),
                directory.resolve().relative_to(args.corpus.resolve()),
            ),
        )
        for directory, expectation in prepared
    ]
    return int(
        not _print_extractor_report(
            extractor_results, args.price_per_million[0], args.price_per_million[1]
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate the eligibility or extractor leaf against reviewed "
            "expectations, replaying recordings and calling a provider only "
            "with --record."
        )
    )
    parser.add_argument("--leaf", choices=("eligibility", "extractor"), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--record", action="store_true")
    parser.add_argument(
        "--price-per-million",
        type=float,
        nargs=2,
        metavar=("INPUT", "OUTPUT"),
        required=True,
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument(
        "--reasoning-effort",
        choices=REASONING_EFFORTS,
        help=(
            "eligibility only: send this reasoning effort instead of temperature=0; "
            "recordings, reason reviews, and freeze records are bound to it"
        ),
    )
    parser.add_argument("--replays", type=int, default=5)
    parser.add_argument(
        "--partition",
        choices=("development", "exposed_regression", "blind", "all"),
        default="development",
    )
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--recordings-dir", type=Path, default=DEFAULT_RECORDINGS)
    parser.add_argument(
        "--extractor-expectations", type=Path, default=DEFAULT_EXTRACTOR_EXPECTATIONS
    )
    parser.add_argument("--safe-report", type=Path)
    parser.add_argument(
        "--gate-profile",
        choices=("development", "blind", "report"),
        help=(
            "development: all SYS-2028 development gates; blind: release gates for "
            "a fresh blind corpus; report: metrics only (default for "
            "exposed_regression and all)."
        ),
    )
    parser.add_argument(
        "--freeze-record",
        type=Path,
        help="freeze record required before exposed-regression or blind evaluation",
    )
    parser.add_argument(
        "--write-freeze",
        type=Path,
        help="write a freeze record (under output/) when development gates pass",
    )
    parser.add_argument(
        "--reason-dispositions",
        type=Path,
        help="operator dispositions for divergent reasons",
    )
    parser.add_argument(
        "--reason-review-out",
        type=Path,
        help="write divergent reasons for manual review (under output/)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.replays < 5:
            raise RuntimeError("--replays must be at least 5")
        return asyncio.run(_run(args))
    except (ModelCallError, ModelResponseError):
        print("model evaluation failed", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        # These messages name paths, cases, and rules, never posting or model text.
        print(f"evaluation input or output is invalid: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValidationError):
        print("evaluation input or output is invalid", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
