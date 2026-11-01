from __future__ import annotations

import argparse
import asyncio
import html
import math
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
    POLICY_VERSION,
    PROMPT_HASH,
    Decision,
    EligibilityResponse,
    UngroundedEvidenceError,
    build_messages,
    normalize,
    validate,
)
from job_pipeline.cli.common import (
    positive_int,
    require_provider_credential,
    run_async,
)
from job_pipeline.llm import (
    LiteLLMCaller,
    ModelCallError,
    ModelResponseError,
    StructuredCaller,
)
from job_pipeline.settings import DEFAULT_MODEL


@dataclass(frozen=True)
class Posting:
    job_id: int
    content_id: int
    title: str | None
    text: str
    accepted_decision: Decision | None


class ReplayModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PostingRecord(ReplayModel):
    job_id: int
    content_id: int
    title: str | None
    accepted_decision: Decision | None


class GroundingMiss(ReplayModel):
    path: str
    quote: str


class CallRecord(ReplayModel):
    job_id: int
    replay: int = Field(ge=1)
    status: Literal["grounded", "ungrounded", "model_error", "invalid_response"]
    raw_decision: Decision | None = None
    validated_decision: Decision | None = None
    validation_notes: list[str] = Field(default_factory=list)
    ungrounded: list[GroundingMiss] = Field(default_factory=list)
    raw_response_json: str | None = None
    error: str | None = None
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    latency_ms: int = Field(ge=0)


class Recording(ReplayModel):
    policy_version: str
    prompt_hash: str
    model: str
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
        for record in self.records:
            if (record.status == "grounded") != (record.validated_decision is not None):
                raise ValueError("only grounded calls have validated decisions")
            if record.status != "ungrounded" and record.raw_response_json is not None:
                raise ValueError("raw responses are only allowed for ungrounded calls")
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
            SELECT j.id, c.id, c.title, c.text,
                   (SELECT d.decision FROM job_decisions d
                    JOIN job_attempts a ON a.id = d.attempt_id
                    WHERE d.job_id = j.id AND d.is_current = 1 AND a.run_id = ?
                    ORDER BY d.id DESC LIMIT 1)
            FROM jobs j
            JOIN (SELECT DISTINCT job_id FROM job_attempts WHERE run_id = ?) selected
              ON selected.job_id = j.id
            LEFT JOIN job_contents c ON c.id = j.current_content_id AND c.job_id = j.id
            ORDER BY j.id
            """,
            (run_id, run_id),
        ).fetchall()
    if not rows:
        raise ValueError("run has no postings to replay")
    postings: list[Posting] = []
    for job_id, content_id, title, text, accepted in rows:
        if content_id is None or not isinstance(text, str) or not text.strip():
            raise ValueError(f"job {job_id} has no usable content checkpoint")
        if accepted not in (None, "eligible", "ineligible", "unknown"):
            raise ValueError(f"job {job_id} has an invalid accepted decision")
        postings.append(Posting(job_id, content_id, title, text, accepted))
    return postings


def ungrounded_strings(response: EligibilityResponse, text: str) -> list[GroundingMiss]:
    signals = response.signals
    arrays = (
        ("$.evidence", response.evidence),
        ("$.signals.locations_named_evidence", signals.locations_named_evidence),
        ("$.signals.remote_scope_evidence", signals.remote_scope_evidence),
        (
            "$.signals.exhaustive_country_list_evidence",
            signals.exhaustive_country_list_evidence,
        ),
        ("$.signals.work_authorization_evidence", signals.work_authorization_evidence),
    )
    posting = " ".join(normalize(text).split())
    return [
        GroundingMiss(path=f"{path}[{index}]", quote=quote)
        for path, quotes in arrays
        for index, quote in enumerate(quotes)
        if " ".join(normalize(quote).split()) not in posting
    ]


def _check_options(replays: int, concurrency: int, timeout: float) -> None:
    if replays < 1 or concurrency < 1 or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("replays, concurrency and finite timeout must be positive")


async def replay(
    postings: Sequence[Posting],
    caller: StructuredCaller,
    *,
    run_id: str,
    model: str,
    replays: int = 5,
    concurrency: int = 4,
    timeout: float = 60,
) -> Recording:
    _check_options(replays, concurrency, timeout)
    if not postings or len({p.job_id for p in postings}) != len(postings):
        raise ValueError("postings must be nonempty and distinct")
    if any(not p.text.strip() for p in postings):
        raise ValueError("postings must have content checkpoints")
    semaphore = asyncio.Semaphore(concurrency)

    async def call(posting: Posting, index: int) -> CallRecord:
        async with semaphore:
            started = time.monotonic()
            record = CallRecord(
                job_id=posting.job_id, replay=index, status="model_error", latency_ms=0
            )
            try:
                async with asyncio.timeout(timeout):
                    completion = await caller.complete(
                        build_messages(posting.text), EligibilityResponse
                    )
            except TimeoutError:
                record.error = "model call timed out"
            except ModelCallError:
                record.error = "model call failed (provider details omitted)"
            except (ModelResponseError, ValidationError, ValueError):
                record.status = "invalid_response"
                record.error = "invalid structured response (details omitted)"
            else:
                response = completion.parsed
                record.raw_decision = response.decision
                record.prompt_tokens = completion.prompt_tokens
                record.completion_tokens = completion.completion_tokens
                record.latency_ms = completion.latency_ms
                try:
                    decision = validate(response, posting.text)
                except UngroundedEvidenceError:
                    record.status = "ungrounded"
                    record.ungrounded = ungrounded_strings(response, posting.text)
                    record.raw_response_json = completion.raw_json
                else:
                    record.status = "grounded"
                    record.validated_decision = decision.decision
                    record.validation_notes = decision.validation_notes
                return record
            record.latency_ms = round((time.monotonic() - started) * 1000)
            return record

    records = await asyncio.gather(
        *(call(p, index) for p in postings for index in range(1, replays + 1))
    )
    return Recording(
        policy_version=POLICY_VERSION,
        prompt_hash=PROMPT_HASH,
        model=model,
        run_id=run_id,
        replays=replays,
        timestamp=datetime.now(UTC).isoformat(),
        postings=[
            PostingRecord(
                job_id=p.job_id,
                content_id=p.content_id,
                title=p.title,
                accepted_decision=p.accepted_decision,
            )
            for p in postings
        ],
        records=records,
    )


def _markdown(value: str) -> str:
    escaped = html.escape(" ".join(value.split()), quote=True)
    return re.sub(r"([\\`*_{}\[\]()#+.!|~\-])", lambda m: f"&#{ord(m[0])};", escaped)


def _distribution(records: Sequence[CallRecord]) -> str:
    counts = Counter(
        r.validated_decision
        for r in records
        if r.status == "grounded" and r.validated_decision is not None
    )
    return ", ".join(f"{key}: {counts[key]}" for key in sorted(counts)) or "none"


def _mean(records: Sequence[CallRecord], field: str) -> str:
    values = [getattr(r, field) for r in records if getattr(r, field) is not None]
    return (
        f"{sum(values) / len(values):.2f} ({len(values)} measured)" if values else "n/a"
    )


def compare(baseline: Recording, new: Recording) -> str:
    old_jobs = {p.job_id: p for p in baseline.postings}
    new_jobs = {p.job_id: p for p in new.postings}
    if baseline.run_id != new.run_id:
        raise ValueError("recordings have different run ids")
    if old_jobs.keys() != new_jobs.keys():
        raise ValueError("recordings have different job sets")
    if any(
        old_jobs[job].accepted_decision != new_jobs[job].accepted_decision
        for job in old_jobs
    ):
        raise ValueError("recordings have different accepted decisions")
    lines = [f"# Eligibility replay comparison: {_markdown(baseline.run_id)}"]
    for name, recording in (("Baseline", baseline), ("New", new)):
        failures = sum(r.status == "ungrounded" for r in recording.records)
        errors = Counter(
            r.status
            for r in recording.records
            if r.status in {"model_error", "invalid_response"}
        )
        lines.extend(
            [
                f"\n## {name}: {_markdown(recording.policy_version)}",
                f"Model: {_markdown(recording.model)}; "
                f"prompt hash: {_markdown(recording.prompt_hash)}; "
                f"replays: {recording.replays}.",
                f"Grounding failures: {failures}/{len(recording.records)} calls.",
                f"Model errors: {errors['model_error']}; "
                f"invalid responses: {errors['invalid_response']}.",
                f"Mean prompt tokens: {_mean(recording.records, 'prompt_tokens')}; "
                "mean completion tokens: "
                f"{_mean(recording.records, 'completion_tokens')}.",
                "\n### Validated decisions per posting",
                "| Job | Title | Accepted | Distribution |",
                "| --- | --- | --- | --- |",
            ]
        )
        for p in recording.postings:
            records = [r for r in recording.records if r.job_id == p.job_id]
            lines.append(
                f"| {p.job_id} | {_markdown(p.title or '')} | "
                f"{p.accepted_decision or 'none'} | {_distribution(records)} |"
            )
        lines.append("\n### Failing evidence")
        for r in recording.records:
            if r.status == "ungrounded":
                for miss in r.ungrounded:
                    lines.append(
                        f"- Job {r.job_id}, replay {r.replay}, "
                        f"{_markdown(miss.path)}: {_markdown(miss.quote)}"
                    )
        if not failures:
            lines.append("None.")
        lines.append("\n### Differences from accepted decisions")
        differences = []
        missing = []
        for p in recording.postings:
            records = [r for r in recording.records if r.job_id == p.job_id]
            if p.accepted_decision is None:
                missing.append(f"- Job {p.job_id}: {_distribution(records)}")
            elif any(
                r.status == "grounded" and r.validated_decision != p.accepted_decision
                for r in records
            ):
                differences.append(
                    f"- Job {p.job_id}: accepted {p.accepted_decision}; "
                    f"{_distribution(records)} (operator review required)"
                )
        lines.extend(differences or ["None."])
        lines.append("\n### Postings without an accepted decision")
        lines.extend(missing or ["None."])
    lines.append("\n## Previously failing postings under the new contract")
    failing_jobs = sorted(
        {r.job_id for r in baseline.records if r.status == "ungrounded"}
    )
    for job_id in failing_jobs:
        records = [r for r in new.records if r.job_id == job_id]
        grounded = sum(r.status == "grounded" for r in records)
        lines.append(f"- Job {job_id}: grounded {grounded}/{len(records)}")
    if not failing_jobs:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def _positive_timeout(text: str) -> float:
    value = float(text)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("timeout must be finite and positive")
    return value


def _label(text: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", text):
        raise argparse.ArgumentTypeError(
            "label must start with a letter or digit and contain only "
            "letters, digits, '.', '_' or '-' (at most 128 characters)"
        )
    return text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only manual eligibility replay.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser(
        "run", help="Replay stored postings with the current contract"
    )
    run.add_argument("--run", required=True)
    run.add_argument("--replays", type=positive_int, default=5)
    run.add_argument("--model", default=DEFAULT_MODEL)
    run.add_argument("--label", type=_label, required=True)
    run.add_argument("--output", type=Path, default=Path("output/eligibility-replay"))
    run.add_argument("--database", type=Path, default=DATABASE_PATH)
    run.add_argument("--timeout", type=_positive_timeout, default=60)
    run.add_argument("--concurrency", type=positive_int, default=4)
    comparison = commands.add_parser("compare", help="Print a Markdown comparison")
    comparison.add_argument("baseline", type=Path)
    comparison.add_argument("new", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "compare":
            baseline = Recording.model_validate_json(args.baseline.read_text("utf-8"))
            new = Recording.model_validate_json(args.new.read_text("utf-8"))
            print(compare(baseline, new), end="")
            return 0
        postings = load_postings(args.database, args.run)
        args.output.mkdir(parents=True, exist_ok=True)
        path = args.output / f"{args.label}.json"
        try:
            stream = path.open("x", encoding="utf-8")
        except FileExistsError:
            print(
                "recording already exists; choose a different --label", file=sys.stderr
            )
            return 2
        try:
            with stream:
                load_dotenv()
                require_provider_credential(args.model)
                recording = run_async(
                    replay(
                        postings,
                        LiteLLMCaller(args.model, timeout=args.timeout),
                        run_id=args.run,
                        model=args.model,
                        replays=args.replays,
                        concurrency=args.concurrency,
                        timeout=args.timeout,
                    )
                )
                stream.write(recording.model_dump_json(indent=2) + "\n")
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        print(f"Recorded {len(recording.records)} calls.")
        return 0
    except ValidationError:
        print("invalid recording format (details omitted)", file=sys.stderr)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
    except (OSError, sqlite3.Error):
        print(
            "cannot read database/recordings or write output; check paths and schema",
            file=sys.stderr,
        )
    except KeyboardInterrupt:
        return 130
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
