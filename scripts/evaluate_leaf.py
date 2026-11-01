"""Evaluate leaf recordings against reviewed corpus expectations."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from job_eligibility import Decision, EligibilityResponse
from job_eligibility import UngroundedEvidenceError as EligibilityGroundingError
from job_eligibility import build_messages as eligibility_messages
from job_eligibility import normalize as normalize_eligibility
from job_eligibility import validate as validate_eligibility
from job_extractor import ExtractionResponse, JobResult
from job_extractor import UngroundedEvidenceError as ExtractorGroundingError
from job_extractor import build_messages as extractor_messages
from job_extractor import validate as validate_extraction
from job_pipeline.llm import (
    LiteLLMCaller,
    RecordingCaller,
    ReplayCaller,
    StructuredCaller,
)

ROOT = Path(__file__).resolve().parents[1]
POSTINGS = ROOT / "tests" / "fixtures" / "postings"
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


@dataclass(frozen=True)
class Evaluation:
    expected: Decision
    actual: Decision | None
    reason: str
    grounding_failed: bool
    must_cite_matched: bool
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: int
    name: str


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
    return model.replace("/", "__").replace("\\", "__")


def _directories() -> list[Path]:
    if not POSTINGS.is_dir():
        return []
    return sorted(path.parent for path in POSTINGS.glob("*/content.txt"))


def _reviewed_payload(directory: Path, filename: str) -> dict[str, Any]:
    path = directory / filename
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"missing or invalid expectation: {path}") from exc
    if not isinstance(payload, dict) or payload.get("reviewed") is not True:
        raise RuntimeError(f"expectation is not reviewed: {path}")
    return payload


def _eligibility_expectation(directory: Path) -> tuple[Decision, list[str]]:
    path = directory / "expected_eligibility.json"
    payload = _reviewed_payload(directory, path.name)
    decision = payload.get("decision")
    if decision not in _DECISIONS:
        raise RuntimeError(f"invalid expected decision: {path}")
    must_cite = payload.get("must_cite")
    if (
        not isinstance(must_cite, list)
        or not must_cite
        or any(not isinstance(value, str) or not value.strip() for value in must_cite)
    ):
        raise RuntimeError(f"invalid eligibility expectation: {path}")
    return cast(Decision, decision), must_cite


def _expectation(directory: Path) -> Decision:
    return _eligibility_expectation(directory)[0]


def _grounding_normalize(value: str) -> str:
    return " ".join(normalize_eligibility(value).split())


def _matches_must_cite(evidence: list[str], alternatives: list[str]) -> bool:
    normalized_evidence = [_grounding_normalize(value) for value in evidence]
    return any(
        _grounding_normalize(alternative) in evidence_value
        for alternative in alternatives
        for evidence_value in normalized_evidence
    )


def _extractor_directories(directories: list[Path]) -> list[Path]:
    return [
        directory
        for directory in directories
        if _expectation(directory) != "ineligible"
    ]


def _extractor_expectation(directory: Path) -> dict[str, Any]:
    path = directory / "expected_extraction.json"
    payload = _reviewed_payload(directory, path.name)
    if not isinstance(payload.get("metadata"), dict):
        raise RuntimeError(f"invalid extraction expectation: {path}")
    for field in _LIST_THRESHOLDS:
        if not isinstance(payload.get(field), list):
            raise RuntimeError(f"invalid extraction expectation: {path}")
    if not isinstance(payload.get("technologies"), list):
        raise RuntimeError(f"invalid extraction expectation: {path}")
    return payload


def _caller(
    directory: Path, leaf: str, model: str, record: bool, timeout: float
) -> StructuredCaller:
    recordings = directory / "responses" / leaf
    key = _model_key(model)
    if record:
        return RecordingCaller(
            LiteLLMCaller(model, timeout=timeout), recordings, key=key
        )
    return ReplayCaller(recordings, key=key)


async def _evaluate_one(
    directory: Path,
    model: str,
    record: bool,
    timeout: float,
) -> Evaluation:
    text = (directory / "content.txt").read_text(encoding="utf-8")
    caller = _caller(directory, "eligibility", model, record, timeout)
    completion = await caller.complete(eligibility_messages(text), EligibilityResponse)
    expected, must_cite = _eligibility_expectation(directory)
    must_cite_matched = _matches_must_cite(completion.parsed.evidence, must_cite)
    try:
        decision = validate_eligibility(completion.parsed, text)
    except EligibilityGroundingError:
        return Evaluation(
            expected=expected,
            actual=None,
            reason=completion.parsed.reason,
            grounding_failed=True,
            must_cite_matched=must_cite_matched,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            latency_ms=completion.latency_ms,
            name=directory.name,
        )
    return Evaluation(
        expected=expected,
        actual=decision.decision,
        reason=decision.reason,
        grounding_failed=False,
        must_cite_matched=must_cite_matched,
        prompt_tokens=completion.prompt_tokens,
        completion_tokens=completion.completion_tokens,
        latency_ms=completion.latency_ms,
        name=directory.name,
    )


async def _evaluate_extractor_one(
    directory: Path,
    model: str,
    record: bool,
    timeout: float,
) -> ExtractorEvaluation:
    text = (directory / "content.txt").read_text(encoding="utf-8")
    caller = _caller(directory, "extractor", model, record, timeout)
    completion = await caller.complete(extractor_messages(text), ExtractionResponse)
    try:
        result = validate_extraction(completion.parsed, text)
    except ExtractorGroundingError:
        result = None
    return ExtractorEvaluation(
        expected=_extractor_expectation(directory),
        actual=result,
        grounding_failed=result is None,
        prompt_tokens=completion.prompt_tokens,
        completion_tokens=completion.completion_tokens,
        latency_ms=completion.latency_ms,
        name=directory.name,
    )


def _usage(results: list[Any], input_price: float, output_price: float) -> str:
    prompt_tokens = sum(result.prompt_tokens or 0 for result in results)
    completion_tokens = sum(result.completion_tokens or 0 for result in results)
    cost = prompt_tokens * input_price / 1_000_000
    cost += completion_tokens * output_price / 1_000_000
    return (
        f"prompt_tokens={prompt_tokens} completion_tokens={completion_tokens} "
        f"estimated_cost={cost:.6f}"
    )


def _latency(results: list[Any]) -> str:
    total = sum(result.latency_ms for result in results)
    per_posting = total / len(results) if results else 0.0
    return f"latency_ms_total={total} latency_ms_per_posting={per_posting:.1f}"


def _print_report(
    results: list[Evaluation], input_price: float, output_price: float
) -> bool:
    matrix = {expected: dict.fromkeys(_DECISIONS, 0) for expected in _DECISIONS}
    grounding_failures = 0
    must_cite_failures = 0
    for result in results:
        grounding_failures += int(result.grounding_failed)
        must_cite_failures += int(not result.must_cite_matched)
        if result.actual is not None:
            matrix[result.expected][result.actual] += 1
        if not result.must_cite_matched:
            print(f"must_cite_mismatch {result.name}")
        if result.actual != result.expected:
            print(
                f"mismatch {result.name}: expected={result.expected} "
                f"actual={result.actual or 'grounding_failed'} reason={result.reason}"
            )
    print("expected\\actual eligible ineligible unknown")
    for expected in _DECISIONS:
        values = " ".join(str(matrix[expected][actual]) for actual in _DECISIONS)
        print(f"{expected} {values}")
    usage = _usage(results, input_price, output_price)
    print(
        f"grounding_failures={grounding_failures} "
        f"must_cite_failures={must_cite_failures} {usage} {_latency(results)}"
    )
    return (
        matrix["eligible"]["ineligible"] == 0
        and matrix["ineligible"]["eligible"] == 0
        and matrix["eligible"]["unknown"] <= 2
        and grounding_failures == 0
        and must_cite_failures == 0
    )


def _key(value: Any) -> str:
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, default=str).casefold()
    return str(value).casefold().strip()


def _fact_value(result: JobResult, field: str) -> dict[str, Any]:
    fact = getattr(result, field)
    dumped = fact.model_dump(mode="json")
    return {"state": dumped["state"], "value": dumped["value"]}


def _actual_list(result: JobResult, field: str) -> set[str]:
    values = getattr(result, field).value or []
    return {_key(value.text if hasattr(value, "text") else value) for value in values}


def _expected_technologies(values: list[Any]) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for value in values:
        if isinstance(value, dict):
            result.add((_key(value.get("name")), _key(value.get("status"))))
        else:
            result.add((_key(value), ""))
    return result


def _actual_technologies(result: JobResult) -> set[tuple[str, str]]:
    return {
        (
            _key(item.canonical or item.mentions[0].mention),
            _key(item.status),
        )
        for item in result.technologies_normalized
    }


def _rates(expected: set[Any], actual: set[Any]) -> tuple[int, int, int]:
    return len(expected & actual), len(actual), len(expected)


def _ratio(matches: int, total: int) -> float:
    return matches / total if total else 1.0


def _print_extractor_report(
    results: list[ExtractorEvaluation], input_price: float, output_price: float
) -> bool:
    metadata: dict[str, list[int]] = {}
    lists = {field: [0, 0, 0] for field in _LIST_THRESHOLDS}
    technology = [0, 0, 0]
    grounding_failures = 0
    failed_fields = 0
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
            if _key(_fact_value(actual, field)) == _key(expected):
                counts[0] += 1
            else:
                print(f"metadata_mismatch {evaluation.name} field={field}")
        for field in _LIST_THRESHOLDS:
            expected = {_key(value) for value in evaluation.expected[field]}
            matched, predicted, wanted = _rates(expected, _actual_list(actual, field))
            lists[field][0] += matched
            lists[field][1] += predicted
            lists[field][2] += wanted
        expected_technology = _expected_technologies(
            evaluation.expected["technologies"]
        )
        matched, predicted, wanted = _rates(
            expected_technology, _actual_technologies(actual)
        )
        technology[0] += matched
        technology[1] += predicted
        technology[2] += wanted
    accepted = grounding_failures == 0
    for field, (matches, total) in sorted(metadata.items()):
        exact = _ratio(matches, total)
        print(f"metadata {field} exact={exact:.3f} ({matches}/{total})")
        threshold = _METADATA_THRESHOLDS.get(field)
        if threshold is not None:
            accepted = accepted and exact >= threshold
    for field, (matches, predicted, expected) in lists.items():
        precision = _ratio(matches, predicted)
        recall = _ratio(matches, expected)
        print(f"list {field} precision={precision:.3f} recall={recall:.3f}")
        minimum_precision, minimum_recall = _LIST_THRESHOLDS[field]
        accepted = (
            accepted and precision >= minimum_precision and recall >= minimum_recall
        )
    precision = _ratio(technology[0], technology[1])
    recall = _ratio(technology[0], technology[2])
    print(f"technologies precision={precision:.3f} recall={recall:.3f}")
    accepted = accepted and precision >= 0.9 and recall >= 0.9
    print(
        f"failed_fields={failed_fields} grounding_failures={grounding_failures} "
        f"{_usage(results, input_price, output_price)} {_latency(results)}"
    )
    return accepted


async def _run(args: argparse.Namespace) -> int:
    directories = _directories()
    if not directories:
        raise RuntimeError(f"posting corpus is unavailable: {POSTINGS}")
    if args.leaf == "eligibility":
        eligibility_results = [
            await _evaluate_one(directory, args.model, args.record, args.timeout)
            for directory in directories
        ]
        accepted = _print_report(
            eligibility_results,
            args.price_per_million[0],
            args.price_per_million[1],
        )
    else:
        directories = _extractor_directories(directories)
        extractor_results = [
            await _evaluate_extractor_one(
                directory, args.model, args.record, args.timeout
            )
            for directory in directories
        ]
        accepted = _print_extractor_report(
            extractor_results,
            args.price_per_million[0],
            args.price_per_million[1],
        )
    return int(not accepted)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
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
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return asyncio.run(_run(args))
    except RuntimeError as exc:
        print(exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
