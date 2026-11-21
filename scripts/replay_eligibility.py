"""Read-only manual replay of stored postings under the one-shot contract.

``run`` replays every posting a pipeline run decided, at least five times, and
records each call's decision, evidence counts, structural consistency, and safe
grounding diagnostics. Raw provider responses stay in per-call sidecars under
the ignored ``output/`` directory and are never written to the database.
``compare`` prints a safe Markdown comparison of two recordings of one run.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import json
import math
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from job_discovery.settings import DATABASE_PATH
from job_eligibility import (
    ELIGIBILITY_PROMPT_HASH,
    ELIGIBILITY_VERSION,
    Decision,
    EligibilityResponse,
    UngroundedEvidenceError,
    build_messages,
    collect_grounding_misses,
    is_missing_information,
    structural_errors,
    validate,
)
from job_pipeline.cli.common import positive_int, require_provider_credential, run_async
from job_pipeline.llm import (
    LiteLLMCaller,
    ModelCallError,
    ModelResponseError,
    StructuredCaller,
)
from job_pipeline.settings import DEFAULT_MODEL

RECORDING_VERSION = 3
PROJECT_OUTPUT = Path(__file__).resolve().parents[1] / "output"


@dataclass(frozen=True)
class Posting:
    job_id: int
    content_id: int
    content_hash: str
    text: str
    accepted_decision: Decision | None
    accepted_contract: str | None


class ReplayModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PostingRecord(ReplayModel):
    job_id: int
    content_id: int
    content_hash: str
    accepted_decision: Decision | None
    accepted_contract: str | None


class GroundingMiss(ReplayModel):
    path: str
    reason: str
    value_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    value_characters: int = Field(ge=0)


CallStatus = Literal[
    "grounded", "inconsistent", "ungrounded", "model_error", "invalid_response"
]
_PARSED_STATUSES = frozenset({"grounded", "inconsistent", "ungrounded"})


class CallRecord(ReplayModel):
    job_id: int
    replay: int = Field(ge=1)
    status: CallStatus
    decision: Decision | None = None
    missing_information: bool | None = None
    supporting_count: int | None = Field(default=None, ge=0)
    conflicting_count: int | None = Field(default=None, ge=0)
    structural_errors: list[str] = Field(default_factory=list)
    grounding_misses: list[GroundingMiss] = Field(default_factory=list)
    raw_response_path: str | None = None
    error_category: Literal["timeout", "provider", "invalid_response"] | None = None
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    latency_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def status_shape(self) -> CallRecord:
        if (self.status == "grounded") != (self.decision is not None):
            raise ValueError("only grounded calls have accepted decisions")
        parsed = self.status in _PARSED_STATUSES
        if parsed != (
            self.supporting_count is not None
            and self.conflicting_count is not None
            and self.missing_information is not None
        ):
            raise ValueError("only parsed calls have evidence counts")
        if (self.status == "inconsistent") != bool(self.structural_errors):
            raise ValueError("only inconsistent calls have structural errors")
        if (self.status == "ungrounded") != bool(self.grounding_misses):
            raise ValueError("only ungrounded calls have grounding misses")
        if self.raw_response_path is not None:
            path = Path(self.raw_response_path)
            if path.is_absolute() or ".." in path.parts or path.suffix != ".json":
                raise ValueError("raw response path must be a safe relative JSON path")
        return self


class Recording(ReplayModel):
    recording_version: Literal[3] = 3
    eligibility_version: str
    eligibility_prompt_hash: str
    requested_model: str
    run_id: str
    replays: int = Field(ge=1)
    timestamp: str
    postings: list[PostingRecord]
    records: list[CallRecord]

    @model_validator(mode="after")
    def check_calls(self) -> Recording:
        job_ids = {posting.job_id for posting in self.postings}
        expected = {
            (job_id, replay)
            for job_id in job_ids
            for replay in range(1, self.replays + 1)
        }
        actual = {(record.job_id, record.replay) for record in self.records}
        if (
            not job_ids
            or len(job_ids) != len(self.postings)
            or actual != expected
            or len(actual) != len(self.records)
        ):
            raise ValueError("recording must contain every posting/replay exactly once")
        return self


def load_postings(database: Path, run_id: str) -> list[Posting]:
    if not database.is_file():
        raise ValueError("database does not exist; pass --database PATH")
    with closing(
        sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
    ) as connection:
        connection.execute("BEGIN")
        if (
            connection.execute(
                "SELECT 1 FROM pipeline_runs WHERE id = ?", (run_id,)
            ).fetchone()
            is None
        ):
            raise ValueError("unknown run; pass an existing pipeline run id")
        rows = connection.execute(
            """
            WITH selected AS (
                SELECT a.*
                FROM job_attempts a
                JOIN (
                    SELECT job_id, MAX(id) AS attempt_id
                    FROM job_attempts
                    WHERE run_id = ?
                    GROUP BY job_id
                ) latest ON latest.attempt_id = a.id
            ), bound AS (
                SELECT selected.job_id,
                       (SELECT d.id FROM job_decisions d
                        JOIN job_attempts da ON da.id = d.attempt_id
                        WHERE da.run_id = selected.run_id
                          AND d.job_id = selected.job_id
                        ORDER BY d.id DESC LIMIT 1) AS decision_id,
                       COALESCE(
                           (SELECT d.content_id FROM job_decisions d
                            JOIN job_attempts da ON da.id = d.attempt_id
                            WHERE da.run_id = selected.run_id
                              AND d.job_id = selected.job_id
                            ORDER BY d.id DESC LIMIT 1),
                           (SELECT c.id FROM job_contents c
                            JOIN pipeline_runs r ON r.id = selected.run_id
                            WHERE c.job_id = selected.job_id
                              AND c.retrieved_at <= COALESCE(
                                  (SELECT MIN(next_attempt.started_at)
                                   FROM job_attempts next_attempt
                                   WHERE next_attempt.job_id = selected.job_id
                                     AND (
                                         next_attempt.started_at > selected.started_at
                                         OR (
                                             next_attempt.started_at =
                                                 selected.started_at
                                             AND next_attempt.id > selected.id
                                         )
                                     )),
                                  selected.finished_at, r.finished_at, CURRENT_TIMESTAMP
                              )
                            ORDER BY c.retrieved_at DESC, c.id DESC LIMIT 1)
                       ) AS content_id
                FROM selected
            )
            SELECT bound.job_id, c.id, c.content_hash, c.text,
                   d.decision, d.policy_version
            FROM bound
            LEFT JOIN job_contents c
              ON c.id = bound.content_id AND c.job_id = bound.job_id
            LEFT JOIN job_decisions d ON d.id = bound.decision_id
            ORDER BY bound.job_id
            """,
            (run_id,),
        ).fetchall()
    if not rows:
        raise ValueError("run has no postings to replay")
    postings: list[Posting] = []
    for job_id, content_id, content_hash, text, accepted, contract in rows:
        if (
            content_id is None
            or not isinstance(text, str)
            or not text.strip()
            or not isinstance(content_hash, str)
        ):
            raise ValueError(f"job {job_id} has no usable content checkpoint")
        if accepted not in (None, "eligible", "ineligible", "unknown"):
            raise ValueError(f"job {job_id} has an invalid accepted decision")
        if contract is not None and not isinstance(contract, str):
            raise ValueError(f"job {job_id} has an invalid accepted contract")
        postings.append(
            Posting(job_id, content_id, content_hash, text, accepted, contract)
        )
    return postings


def _recording_key(model: str, run_id: str, posting: Posting, replay_index: int) -> str:
    payload = {
        "model": model,
        "eligibility_version": ELIGIBILITY_VERSION,
        "eligibility_prompt_hash": ELIGIBILITY_PROMPT_HASH,
        "run_id": run_id,
        "job_id": posting.job_id,
        "content_id": posting.content_id,
        "content_hash": posting.content_hash,
        "replay": replay_index,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _output_child(root: Path, *parts: str | Path) -> Path:
    child = root.joinpath(*parts).resolve()
    try:
        child.relative_to(root.resolve())
        child.relative_to(PROJECT_OUTPUT.resolve())
    except ValueError as exc:
        raise ValueError(
            "artifact must remain under the ignored output directory"
        ) from exc
    return child


def _write_json(path: Path, payload: object, reservation: Path) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        reservation.unlink(missing_ok=True)


def _reserve(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    reservation = path.with_name(f".{path.name}.reserved")
    descriptor = os.open(reservation, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(str(os.getpid()))
    return reservation


def _sidecar_contract(
    key: str, model: str, run_id: str, posting: Posting, replay_index: int
) -> dict[str, object]:
    return {
        "recording_version": 2,
        "key": key,
        "requested_model": model,
        "eligibility_version": ELIGIBILITY_VERSION,
        "eligibility_prompt_hash": ELIGIBILITY_PROMPT_HASH,
        "run_id": run_id,
        "job_id": posting.job_id,
        "content_id": posting.content_id,
        "content_hash": posting.content_hash,
        "request": "replay",
        "replay": replay_index,
    }


def _sidecar_path(directory: Path, key: str) -> Path:
    return _output_child(directory, f"{key}.json")


def _write_sidecar(
    directory: Path, key: str, raw_json: str, created_paths: set[Path] | None = None
) -> str:
    directory = _output_child(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = _sidecar_path(directory, key)
    reservation = _reserve(path)
    try:
        _write_json(path, {"raw_json": raw_json}, reservation)
    except BaseException:
        reservation.unlink(missing_ok=True)
        raise
    return (Path(directory.name) / path.name).as_posix()


def _save_call_artifact(
    path: Path,
    contract: dict[str, object],
    record: CallRecord,
    raw_json: str | None,
    reservation: Path,
) -> None:
    _write_json(
        path,
        {**contract, "record": record.model_dump(mode="json"), "raw_json": raw_json},
        reservation,
    )


def _parsed_record(
    response: EligibilityResponse,
    posting: Posting,
    *,
    replay_index: int,
    raw_response_path: str | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    latency_ms: int,
) -> CallRecord:
    """Classify one parsed response: inconsistent, ungrounded, or grounded."""
    shared: dict[str, object] = {
        "job_id": posting.job_id,
        "replay": replay_index,
        "missing_information": is_missing_information(response),
        "supporting_count": len(response.supporting_evidence),
        "conflicting_count": len(response.conflicting_evidence),
        "raw_response_path": raw_response_path,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "latency_ms": latency_ms,
    }
    errors = structural_errors(response)
    if errors:
        return CallRecord.model_validate(
            {**shared, "status": "inconsistent", "structural_errors": list(errors)}
        )
    misses = collect_grounding_misses(response, posting.text)
    if misses:
        return CallRecord.model_validate(
            {
                **shared,
                "status": "ungrounded",
                "grounding_misses": [
                    GroundingMiss(
                        path=miss.path,
                        reason=miss.reason,
                        value_sha256=miss.value_sha256,
                        value_characters=miss.value_characters,
                    )
                    for miss in misses
                ],
            }
        )
    try:
        verdict = validate(response, posting.text)
    except UngroundedEvidenceError:
        raise AssertionError("grounding collector and validator disagree") from None
    return CallRecord.model_validate(
        {**shared, "status": "grounded", "decision": verdict.decision}
    )


def _recomputed_record(
    raw_json: str, posting: Posting, record: CallRecord
) -> CallRecord:
    try:
        response = EligibilityResponse.model_validate_json(raw_json)
    except (ValidationError, ValueError) as exc:
        raise ValueError("invalid replay call artifact") from exc
    return _parsed_record(
        response,
        posting,
        replay_index=record.replay,
        raw_response_path=record.raw_response_path,
        prompt_tokens=record.prompt_tokens,
        completion_tokens=record.completion_tokens,
        latency_ms=record.latency_ms,
    )


def _load_call_artifact(
    path: Path, contract: dict[str, object], posting: Posting
) -> CallRecord:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("invalid replay call artifact") from exc
    if not isinstance(payload, dict) or any(
        payload.get(name) != value for name, value in contract.items()
    ):
        raise ValueError("incompatible replay call artifact")
    record = CallRecord.model_validate(payload.get("record"))
    raw_json = payload.get("raw_json")
    if record.status in _PARSED_STATUSES:
        if not isinstance(raw_json, str):
            raise ValueError("invalid replay call artifact")
        if record.job_id != posting.job_id or (
            _recomputed_record(raw_json, posting, record) != record
        ):
            raise ValueError("replay call artifact summary does not match raw response")
    elif raw_json is not None:
        raise ValueError("invalid replay call artifact")
    return record


def _check_options(replays: int, concurrency: int, timeout: float) -> None:
    if replays < 5 or concurrency < 1 or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError(
            "replays must be at least 5; concurrency and timeout must be positive"
        )


def _all_call_artifacts_complete(
    directory: Path,
    postings: Sequence[Posting],
    model: str,
    run_id: str,
    replays: int,
) -> bool:
    for posting in postings:
        for index in range(1, replays + 1):
            key = _recording_key(model, run_id, posting, index)
            path = _sidecar_path(directory, key)
            if not path.exists():
                return False
            _load_call_artifact(
                path, _sidecar_contract(key, model, run_id, posting, index), posting
            )
    return True


async def replay(
    postings: Sequence[Posting],
    caller: StructuredCaller | None,
    *,
    run_id: str,
    model: str,
    raw_directory: Path | None = None,
    replays: int = 5,
    concurrency: int = 4,
    timeout: float = 60,
    created_paths: set[Path] | None = None,
) -> Recording:
    _check_options(replays, concurrency, timeout)
    if not postings or len({posting.job_id for posting in postings}) != len(postings):
        raise ValueError("postings must be nonempty and distinct")
    if any(not posting.text.strip() for posting in postings):
        raise ValueError("postings must have content checkpoints")
    semaphore = asyncio.Semaphore(concurrency)

    async def call(posting: Posting, index: int) -> CallRecord:
        key = _recording_key(model, run_id, posting, index)
        contract = _sidecar_contract(key, model, run_id, posting, index)
        path = _sidecar_path(raw_directory, key) if raw_directory is not None else None
        if path is not None and path.exists():
            return _load_call_artifact(path, contract, posting)
        reservation = _reserve(path) if path is not None else None
        raw_json: str | None = None
        async with semaphore:
            started = time.monotonic()
            try:
                if caller is None:
                    raise ValueError("provider caller unavailable for missing artifact")
                async with asyncio.timeout(timeout):
                    completion = await caller.complete(
                        build_messages(posting.text), EligibilityResponse
                    )
            except TimeoutError:
                record = CallRecord(
                    job_id=posting.job_id,
                    replay=index,
                    status="model_error",
                    error_category="timeout",
                    latency_ms=round((time.monotonic() - started) * 1000),
                )
            except ModelCallError:
                record = CallRecord(
                    job_id=posting.job_id,
                    replay=index,
                    status="model_error",
                    error_category="provider",
                    latency_ms=round((time.monotonic() - started) * 1000),
                )
            except (ModelResponseError, ValidationError, ValueError):
                record = CallRecord(
                    job_id=posting.job_id,
                    replay=index,
                    status="invalid_response",
                    error_category="invalid_response",
                    latency_ms=round((time.monotonic() - started) * 1000),
                )
            else:
                raw_json = completion.raw_json
                relative = (
                    (Path(raw_directory.name) / path.name).as_posix()
                    if raw_directory is not None and path is not None
                    else None
                )
                record = _parsed_record(
                    completion.parsed,
                    posting,
                    replay_index=index,
                    raw_response_path=relative,
                    prompt_tokens=completion.prompt_tokens,
                    completion_tokens=completion.completion_tokens,
                    latency_ms=completion.latency_ms,
                )
        if path is not None and reservation is not None:
            _save_call_artifact(path, contract, record, raw_json, reservation)
        return record

    try:
        records = await asyncio.gather(
            *(
                call(posting, index)
                for posting in postings
                for index in range(1, replays + 1)
            )
        )
    except BaseException:
        if raw_directory is not None and raw_directory.is_dir():
            for reservation in raw_directory.glob(".*.reserved"):
                reservation.unlink(missing_ok=True)
        raise
    return Recording(
        eligibility_version=ELIGIBILITY_VERSION,
        eligibility_prompt_hash=ELIGIBILITY_PROMPT_HASH,
        requested_model=model,
        run_id=run_id,
        replays=replays,
        timestamp=datetime.now(UTC).isoformat(),
        postings=[
            PostingRecord(
                job_id=posting.job_id,
                content_id=posting.content_id,
                content_hash=posting.content_hash,
                accepted_decision=posting.accepted_decision,
                accepted_contract=posting.accepted_contract,
            )
            for posting in postings
        ],
        records=records,
    )


def _markdown(value: str) -> str:
    escaped = html.escape(" ".join(value.split()), quote=True)
    return re.sub(
        r"([\\`*_{}\[\]()#+.!|~\-])", lambda match: f"&#{ord(match[0])};", escaped
    )


def _distribution(records: Sequence[CallRecord]) -> str:
    counts = Counter(
        (
            f"{record.decision}/"
            + (
                "missing_information"
                if record.missing_information
                else f"s{record.supporting_count}c{record.conflicting_count}"
            )
        )
        if record.status == "grounded"
        else record.status
        for record in records
    )
    return ", ".join(f"{key}: {counts[key]}" for key in sorted(counts)) or "none"


def _mean(records: Sequence[CallRecord], field: str) -> str:
    values = [
        getattr(record, field)
        for record in records
        if getattr(record, field) is not None
    ]
    return (
        f"{sum(values) / len(values):.2f} ({len(values)} measured)" if values else "n/a"
    )


def _compatible(baseline: Recording, new: Recording) -> None:
    if baseline.run_id != new.run_id:
        raise ValueError("recordings have different run ids")
    old = {posting.job_id: posting for posting in baseline.postings}
    current = {posting.job_id: posting for posting in new.postings}
    if old.keys() != current.keys():
        raise ValueError("recordings have different job sets")
    for job_id in old:
        first = old[job_id]
        second = current[job_id]
        if (first.content_id, first.content_hash) != (
            second.content_id,
            second.content_hash,
        ):
            raise ValueError("recordings have different content checkpoints")
        if (first.accepted_decision, first.accepted_contract) != (
            second.accepted_decision,
            second.accepted_contract,
        ):
            raise ValueError("recordings have different accepted metadata")


def compare(baseline: Recording, new: Recording) -> str:
    _compatible(baseline, new)
    lines = [f"# Eligibility replay comparison: {_markdown(baseline.run_id)}"]
    for name, recording in (("Baseline", baseline), ("New", new)):
        accepted = {
            posting.job_id: posting.accepted_decision for posting in recording.postings
        }
        statuses = Counter(record.status for record in recording.records)
        model = _markdown(recording.requested_model)
        version = _markdown(recording.eligibility_version)
        prompt_hash = _markdown(recording.eligibility_prompt_hash)
        grounding = statuses["ungrounded"]
        inconsistent = statuses["inconsistent"]
        grounded = [
            record for record in recording.records if record.status == "grounded"
        ]
        agreement = sum(
            record.decision == accepted[record.job_id]
            for record in grounded
            if accepted.get(record.job_id) is not None
        )
        compared = sum(accepted.get(record.job_id) is not None for record in grounded)
        invalid = statuses["invalid_response"]
        prompt_tokens = _mean(recording.records, "prompt_tokens")
        completion_tokens = _mean(recording.records, "completion_tokens")
        latency = _mean(recording.records, "latency_ms")
        lines.extend(
            [
                f"\n## {name}",
                f"Model: {model}; eligibility version: {version}; "
                f"prompt hash: {prompt_hash}; replays: {recording.replays}.",
                f"Grounding failures: {grounding}/{len(recording.records)} calls; "
                f"structurally inconsistent: {inconsistent}.",
                f"Model errors: {statuses['model_error']}; "
                f"invalid responses: {invalid}.",
                f"Agreement with accepted decisions: {agreement}/{compared} "
                "grounded calls (accepted decisions may predate this contract).",
                f"Mean prompt tokens: {prompt_tokens}; mean completion tokens: "
                f"{completion_tokens}; mean latency ms: {latency}.",
                "\n### Outcomes per posting (decision/supporting+conflicting counts)",
                "| Job | Accepted decision | Accepted contract | Distribution |",
                "| --- | --- | --- | --- |",
            ]
        )
        for posting in recording.postings:
            records = [
                record
                for record in recording.records
                if record.job_id == posting.job_id
            ]
            decision = posting.accepted_decision or "none"
            contract = _markdown(posting.accepted_contract or "none")
            distribution = _markdown(_distribution(records))
            lines.append(
                f"| {posting.job_id} | {decision} | {contract} | {distribution} |"
            )
        miss_counts = Counter(
            (miss.path, miss.reason)
            for record in recording.records
            for miss in record.grounding_misses
        )
        lines.append("\n### Grounding misses by safe path and reason")
        lines.extend(
            f"- {_markdown(path)} / {_markdown(reason)}: {count}"
            for (path, reason), count in sorted(miss_counts.items())
        )
        if not miss_counts:
            lines.append("None.")
        error_counts = Counter(
            error.split(" ", 1)[1] if error.startswith("$.") else error
            for record in recording.records
            for error in record.structural_errors
        )
        lines.append("\n### Structural errors by category")
        lines.extend(
            f"- {_markdown(error)}: {count}"
            for error, count in sorted(error_counts.items())
        )
        if not error_counts:
            lines.append("None.")
    return "\n".join(lines) + "\n"


def _positive_timeout(text: str) -> float:
    value = float(text)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("timeout must be finite and positive")
    return value


def _label(text: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", text):
        raise argparse.ArgumentTypeError("label must be a safe filename component")
    return text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only manual one-shot eligibility replay."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser(
        "run", help="Replay stored postings with the current eligibility contract"
    )
    run.add_argument("--run", required=True)
    run.add_argument("--replays", type=positive_int, default=5)
    run.add_argument("--model", default=DEFAULT_MODEL)
    run.add_argument("--label", type=_label, required=True)
    run.add_argument(
        "--output", type=Path, default=PROJECT_OUTPUT / "eligibility-replay"
    )
    run.add_argument("--database", type=Path, default=DATABASE_PATH)
    run.add_argument("--timeout", type=_positive_timeout, default=60)
    run.add_argument("--concurrency", type=positive_int, default=4)
    comparison = commands.add_parser("compare", help="Print a safe Markdown comparison")
    comparison.add_argument("baseline", type=Path)
    comparison.add_argument("new", type=Path)
    return parser


def _load_recording(path: Path) -> Recording:
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("invalid recording format (details omitted)") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("recording_version") != RECORDING_VERSION
    ):
        version = (
            payload.get("recording_version") if isinstance(payload, dict) else None
        )
        if version in (None, 1, 2):
            raise ValueError(
                "recording versions 1 and 2 predate the one-shot contract; "
                "compare two version 3 recordings"
            )
        raise ValueError("unsupported recording version")
    return Recording.model_validate(payload)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "compare":
            print(
                compare(_load_recording(args.baseline), _load_recording(args.new)),
                end="",
            )
            return 0
        _check_options(args.replays, args.concurrency, args.timeout)
        postings = load_postings(args.database, args.run)
        output = _output_child(args.output)
        output.mkdir(parents=True, exist_ok=True)
        label_key = hashlib.sha256(args.label.encode()).hexdigest()
        aggregate_path = _output_child(output, f"{label_key}.json")
        raw_directory = _output_child(output, f"{label_key}.raw")
        if aggregate_path.exists():
            recording = _load_recording(aggregate_path)
            expected_postings = [
                PostingRecord(
                    job_id=posting.job_id,
                    content_id=posting.content_id,
                    content_hash=posting.content_hash,
                    accepted_decision=posting.accepted_decision,
                    accepted_contract=posting.accepted_contract,
                )
                for posting in postings
            ]
            if (
                recording.requested_model != args.model
                or recording.run_id != args.run
                or recording.replays != args.replays
                or recording.eligibility_version != ELIGIBILITY_VERSION
                or recording.eligibility_prompt_hash != ELIGIBILITY_PROMPT_HASH
                or recording.postings != expected_postings
            ):
                raise ValueError("existing recording is incompatible")
            print(f"Recorded {len(recording.records)} calls.")
            return int(any(record.status != "grounded" for record in recording.records))
        complete = _all_call_artifacts_complete(
            raw_directory, postings, args.model, args.run, args.replays
        )
        caller: StructuredCaller | None = None
        if not complete:
            load_dotenv()
            require_provider_credential(args.model)
            caller = LiteLLMCaller(args.model, timeout=args.timeout)
        raw_directory.mkdir(mode=0o700, exist_ok=True)
        recording = run_async(
            replay(
                postings,
                caller,
                run_id=args.run,
                model=args.model,
                raw_directory=raw_directory,
                replays=args.replays,
                concurrency=args.concurrency,
                timeout=args.timeout,
            )
        )
        reservation = _reserve(aggregate_path)
        _write_json(
            aggregate_path,
            recording.model_dump(mode="json"),
            reservation,
        )
        print(f"Recorded {len(recording.records)} calls.")
        return int(any(record.status != "grounded" for record in recording.records))
    except FileExistsError:
        print("recording already exists; choose a different --label", file=sys.stderr)
    except ValidationError:
        print("invalid recording format (details omitted)", file=sys.stderr)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
    except (OSError, sqlite3.Error):
        print(
            "cannot read database/recordings or write output; check paths and schema",
            file=sys.stderr,
        )
    except (ModelCallError, ModelResponseError):
        print("provider call failed", file=sys.stderr)
    except KeyboardInterrupt:
        return 130
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
