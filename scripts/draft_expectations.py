"""Draft unreviewed expectations for a posting corpus.

``--leaf eligibility`` drafts one-shot evidence obligations
(``expected_evidence.json``) without any model call: each draft is derived from
the operator-reviewed decision and ``must_cite`` quotes in
``expected_eligibility.json``, so labels for a fresh blind corpus are never
seeded by the candidate being evaluated. ``--validate-review-dir`` checks
operator-reviewed obligation files before they are copied into a corpus.

``--leaf extractor`` drafts extraction expectations from a model for operator
review. Every draft is written under the ignored ``output/`` directory, never
over an existing file, and is unreviewed until an operator says otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from job_eligibility import ELIGIBILITY_VERSION, MAX_EVIDENCE_CHARACTERS, occurs_in
from job_extractor import PROMPT_HASH as EXTRACTOR_PROMPT_HASH
from job_extractor import ExtractionResponse, JobResult
from job_extractor import build_messages as extractor_messages
from job_extractor import validate as validate_extraction
from job_pipeline.llm import (
    Completion,
    LiteLLMCaller,
    ModelCallError,
    ModelResponseError,
)

ROOT = Path(__file__).resolve().parents[1]
POSTINGS = ROOT / "tests" / "fixtures" / "postings"
DEFAULT_CORPUS = POSTINGS / "opportunity-screening-v2"
DEFAULT_OUTPUT = ROOT / "output" / "evidence-drafts" / "opportunity-screening-v2"
DEFAULT_RECORDINGS = ROOT / "output" / "draft-recordings"
PROJECT_OUTPUT = ROOT / "output"
EVIDENCE_SCHEMA_VERSION = 1
_LIST_FIELDS = (
    "responsibilities",
    "required_qualifications",
    "preferred_qualifications",
    "benefits",
)
_EVIDENCE_KEYS = {
    "schema_version",
    "case_id",
    "reviewed",
    "review_status",
    "partition",
    "source_content_sha256",
    "decision",
    "eligibility_version",
    "prompt_example",
    "missing_information",
    "obligations",
    "conflict_pairs",
}
_REVIEWED_KEYS = _EVIDENCE_KEYS | {"review_provenance"}
_OBLIGATION_KEYS = {"obligation_id", "role", "critical", "excerpt_alternatives"}


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


def _relative_case(directory: Path, corpus: Path) -> Path:
    try:
        return directory.resolve().relative_to(corpus.resolve())
    except ValueError as exc:
        raise RuntimeError("posting is outside the selected corpus") from exc


def _posting_directories(root: Path | None = None) -> list[Path]:
    selected_root = POSTINGS if root is None else root
    if not selected_root.is_dir():
        return []
    return sorted(
        path.parent
        for path in selected_root.rglob("content.txt")
        if path.parent != selected_root
    )


def _extractor_directories(directories: list[Path]) -> list[Path]:
    selected: list[Path] = []
    for directory in directories:
        content = (directory / "content.txt").read_bytes()
        payload = _eligibility_payload(directory, content)
        if payload["decision"] != "ineligible":
            selected.append(directory)
    return selected


def _draft_contract(model: str, leaf: str, key: str) -> dict[str, object]:
    return {
        "requested_model": model,
        "leaf": leaf,
        **_recording_contract(leaf),
        "request": "draft",
        "replay": 1,
        "recording_key": key,
    }


def _extraction_expectation(
    result: JobResult, model: str, key: str, source_hash: str
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    excluded = {*_LIST_FIELDS, "technologies"}
    for name in ExtractionResponse.model_fields:
        if name not in excluded:
            dumped = getattr(result, name).model_dump(mode="json")
            metadata[name] = {"state": dumped["state"], "value": dumped["value"]}
    components = {}
    for name in _LIST_FIELDS:
        values = getattr(result, name).value or []
        components[name] = [
            item.text if hasattr(item, "text") else item for item in values
        ]
    technologies = [
        {
            "name": technology.canonical or technology.mentions[0].mention,
            "status": technology.status,
        }
        for technology in result.technologies_normalized
    ]
    return {
        "reviewed": False,
        "source_content_sha256": source_hash,
        "draft_contract": _draft_contract(model, "extractor", key),
        "metadata": metadata,
        **components,
        "technologies": technologies,
        "notes": "Draft generated by a model; operator review is required.",
    }


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _eligibility_payload(directory: Path, content: bytes) -> dict[str, Any]:
    path = directory / "expected_eligibility.json"
    try:
        expected = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"missing or invalid eligibility expectation: {path}"
        ) from exc
    source_hash = _sha256(content)
    if (
        not isinstance(expected, dict)
        or expected.get("reviewed") is not True
        or expected.get("decision") not in {"eligible", "ineligible", "unknown"}
        or expected.get("case_id") != directory.name
        or expected.get("partition") not in {"development", "exposed_regression"}
        or expected.get("source_content_sha256") != source_hash
    ):
        raise RuntimeError(f"invalid reviewed eligibility expectation: {path}")
    return expected


def _schema_hash(schema: type[ExtractionResponse]) -> str:
    payload = json.dumps(
        schema.model_json_schema(), sort_keys=True, separators=(",", ":")
    )
    return _sha256(payload.encode())


def _recording_key(
    model: str,
    case_id: str,
    source_hash: str,
    *,
    leaf: str = "extractor",
    replay: int = 1,
) -> str:
    payload = {
        "requested_model": model,
        "leaf": leaf,
        **_recording_contract(leaf),
        "case_id": case_id,
        "source_content_sha256": source_hash,
        "request": "draft",
        "replay": replay,
    }
    return _sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())


def _reserve(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    reservation = path.with_name(f".{path.name}.reserved")
    descriptor = os.open(reservation, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(str(os.getpid()))
    return reservation


def _atomic_json(path: Path, payload: object, reservation: Path) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True, default=str)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        reservation.unlink(missing_ok=True)


def _recording_path(directory: Path, key: str) -> Path:
    return _output_child(directory, f"{key}.json")


def _recording_payload(
    key: str,
    model: str,
    leaf: str,
    case_id: str,
    source_hash: str,
    completion: Completion[Any],
) -> dict[str, object]:
    return {
        "recording_version": 3,
        "key": key,
        "requested_model": model,
        "leaf": leaf,
        **_recording_contract(leaf),
        "case_id": case_id,
        "source_content_sha256": source_hash,
        "request": "draft",
        "replay": 1,
        "raw_json": completion.raw_json,
        "response_model": completion.model,
        "prompt_tokens": completion.prompt_tokens,
        "completion_tokens": completion.completion_tokens,
        "latency_ms": completion.latency_ms,
    }


def _valid_review_provenance(value: object) -> bool:
    required = {"operator", "reviewed_at", "basis"}
    if not isinstance(value, dict) or set(value) != required:
        return False
    if any(not isinstance(value[key], str) or not value[key].strip() for key in value):
        return False
    try:
        datetime.fromisoformat(value["reviewed_at"].replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _recording_contract(leaf: str) -> dict[str, object]:
    if leaf != "extractor":
        raise RuntimeError("only extractor drafts call a model")
    return {
        "prompt_hash": EXTRACTOR_PROMPT_HASH,
        "schema": ExtractionResponse.__name__,
        "schema_hash": _schema_hash(ExtractionResponse),
    }


def _load_recording(
    path: Path, key: str, model: str, case_id: str, source_hash: str
) -> Completion[ExtractionResponse]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("recording is incomplete or malformed") from exc
    expected = {
        "recording_version": 3,
        "key": key,
        "requested_model": model,
        "leaf": "extractor",
        **_recording_contract("extractor"),
        "case_id": case_id,
        "source_content_sha256": source_hash,
        "request": "draft",
        "replay": 1,
    }
    if not isinstance(payload, dict) or any(
        payload.get(name) != value for name, value in expected.items()
    ):
        raise RuntimeError("recording is incompatible")
    raw_json = payload.get("raw_json")
    if not isinstance(raw_json, str):
        raise RuntimeError("recording response is invalid")
    try:
        parsed = ExtractionResponse.model_validate_json(raw_json)
    except (ValidationError, ValueError) as exc:
        raise RuntimeError("recording response is invalid") from exc

    def count(name: str) -> int | None:
        value = payload.get(name)
        return value if isinstance(value, int) else None

    return Completion(
        parsed=parsed,
        raw_json=raw_json,
        model=str(payload.get("response_model", model)),
        prompt_tokens=count("prompt_tokens"),
        completion_tokens=count("completion_tokens"),
        latency_ms=count("latency_ms") or 0,
    )


def _load_draft(path: Path, model: str, key: str, source_hash: str) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("draft is incomplete or malformed") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("source_content_sha256") != source_hash
        or payload.get("draft_contract") != _draft_contract(model, "extractor", key)
    ):
        raise RuntimeError("draft is incompatible")


async def _draft_extractor(
    directory: Path,
    caller: LiteLLMCaller,
    model: str,
    text: str,
    output_root: Path,
    recordings_root: Path,
    corpus: Path,
) -> None:
    relative = _relative_case(directory, corpus)
    output = _output_child(output_root, relative, "expected_extraction.json")
    source_hash = _sha256(text.encode())
    key = _recording_key(model, directory.name, source_hash, leaf="extractor", replay=1)
    recording_root = _output_child(recordings_root, _model_key(model), relative)
    recording = _recording_path(recording_root, key)
    if output.exists():
        _load_draft(output, model, key, source_hash)
        return
    output_reservation: Path | None = _reserve(output)
    recording_reservation: Path | None = None
    try:
        if recording.exists():
            completion = _load_recording(
                recording, key, model, directory.name, source_hash
            )
        else:
            recording_reservation = _reserve(recording)
            completion = await caller.complete(
                extractor_messages(text), ExtractionResponse
            )
            _atomic_json(
                recording,
                _recording_payload(
                    key, model, "extractor", directory.name, source_hash, completion
                ),
                recording_reservation,
            )
            recording_reservation = None
        result = validate_extraction(completion.parsed, text)
        assert output_reservation is not None
        _atomic_json(
            output,
            _extraction_expectation(result, model, key, source_hash),
            output_reservation,
        )
        output_reservation = None
    finally:
        if recording_reservation is not None:
            recording_reservation.unlink(missing_ok=True)
        if output_reservation is not None:
            output_reservation.unlink(missing_ok=True)


def _obligation_draft(directory: Path, content: bytes) -> dict[str, Any]:
    """Derive draft obligations from the reviewed decision and must_cite quotes.

    Eligible and ineligible drafts get one supporting obligation whose
    alternatives are the reviewed quotes. An unknown draft with several quotes
    is treated as a conflict: one ``either`` obligation per quote and a pair
    that must land in different buckets. The operator adjusts roles, adds
    minimal-core alternatives, splits combined permissions, and marks prompt
    examples before setting ``reviewed`` to true.
    """
    expected = _eligibility_payload(directory, content)
    decision = expected["decision"]
    quotes = expected.get("must_cite")
    if not isinstance(quotes, list) or not quotes:
        raise RuntimeError(f"reviewed must_cite quotes are missing: {directory}")
    text = content.decode("utf-8")
    for quote in quotes:
        if not isinstance(quote, str) or not occurs_in(quote, text):
            raise RuntimeError(f"reviewed must_cite quote is not grounded: {directory}")
    case_id = directory.name
    conflict = decision == "unknown" and len(quotes) > 1
    groups: list[tuple[str, list[str]]] = (
        [("either", [quote]) for quote in quotes]
        if conflict
        else [("supporting", list(quotes))]
    )
    obligations = [
        {
            "obligation_id": f"{case_id}-E{index:02d}",
            "role": role,
            "critical": True,
            "excerpt_alternatives": alternatives,
        }
        for index, (role, alternatives) in enumerate(groups, start=1)
    ]
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "case_id": case_id,
        "reviewed": False,
        "review_status": "pending_operator_review",
        "partition": expected["partition"],
        "source_content_sha256": _sha256(content),
        "decision": decision,
        "eligibility_version": ELIGIBILITY_VERSION,
        "prompt_example": None,
        "missing_information": False,
        "obligations": obligations,
        "conflict_pairs": (
            [[item["obligation_id"] for item in obligations[:2]]] if conflict else []
        ),
    }


def _draft_eligibility(
    directory: Path, output_root: Path, corpus: Path
) -> Literal["drafted", "existing"]:
    content = (directory / "content.txt").read_bytes()
    draft = _obligation_draft(directory, content)
    output = _output_child(
        output_root, _relative_case(directory, corpus), "expected_evidence.json"
    )
    if output.exists():
        try:
            existing = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("existing draft is malformed") from exc
        if (
            not isinstance(existing, dict)
            or existing.get("source_content_sha256") != draft["source_content_sha256"]
        ):
            raise RuntimeError("existing draft is incompatible")
        return "existing"
    _atomic_json(output, draft, _reserve(output))
    return "drafted"


def _validate_reviewed_obligations(
    payload: object, directory: Path, content: bytes, path: Path
) -> None:
    expected = _eligibility_payload(directory, content)
    text = content.decode("utf-8")
    if (
        not isinstance(payload, dict)
        or set(payload) != _REVIEWED_KEYS
        or payload.get("schema_version") != EVIDENCE_SCHEMA_VERSION
        or payload.get("reviewed") is not True
        or payload.get("review_status") != "reviewed"
        or not _valid_review_provenance(payload.get("review_provenance"))
        or payload.get("case_id") != directory.name
        or payload.get("partition") != expected["partition"]
        or payload.get("source_content_sha256") != _sha256(content)
        or payload.get("decision") != expected["decision"]
        or payload.get("eligibility_version") != ELIGIBILITY_VERSION
        or not isinstance(payload.get("missing_information"), bool)
        or not (
            payload.get("prompt_example") is None
            or isinstance(payload.get("prompt_example"), str)
        )
    ):
        raise RuntimeError(f"invalid reviewed obligation metadata: {path}")
    values = payload.get("obligations")
    if not isinstance(values, list):
        raise RuntimeError(f"invalid reviewed obligations: {path}")
    roles: dict[str, str] = {}
    cited: set[str] = set()
    for index, value in enumerate(values, start=1):
        alternatives = (
            value.get("excerpt_alternatives") if isinstance(value, dict) else None
        )
        if (
            not isinstance(value, dict)
            or set(value) != _OBLIGATION_KEYS
            or value.get("obligation_id") != f"{directory.name}-E{index:02d}"
            or value.get("role") not in {"supporting", "conflicting", "either"}
            or not isinstance(value.get("critical"), bool)
            or not isinstance(alternatives, list)
            or not alternatives
        ):
            raise RuntimeError(f"invalid reviewed obligation: {path}")
        for alternative in alternatives:
            if (
                not isinstance(alternative, str)
                or len(alternative) > MAX_EVIDENCE_CHARACTERS
                or not occurs_in(alternative, text)
            ):
                raise RuntimeError(f"reviewed obligation is not grounded: {path}")
            cited.add(alternative)
        roles[value["obligation_id"]] = value["role"]
    missing = payload["missing_information"]
    if missing != (not values) or (missing and expected["decision"] != "unknown"):
        raise RuntimeError(f"invalid missing-information obligations: {path}")
    if not missing and not set(expected.get("must_cite", [])) <= cited:
        raise RuntimeError(f"reviewed obligations drop a must_cite quote: {path}")
    pairs = payload.get("conflict_pairs")
    if not isinstance(pairs, list) or any(
        not isinstance(pair, list)
        or len(pair) != 2
        or pair[0] == pair[1]
        or any(roles.get(item) != "either" for item in pair)
        for pair in pairs
    ):
        raise RuntimeError(f"invalid conflict pairs: {path}")
    if pairs and expected["decision"] != "unknown":
        raise RuntimeError(f"conflict pairs require an unknown decision: {path}")


def validate_review_dir(
    corpus: Path, review_dir: Path, case_ids: tuple[str, ...] = ()
) -> int:
    review_dir = _require_output_path(review_dir, "reviewed expectations")
    directories = _selected_directories(corpus, case_ids)
    expected_files = {
        _output_child(
            review_dir, _relative_case(directory, corpus), "expected_evidence.json"
        )
        for directory in directories
    }
    actual_files = {path.resolve() for path in review_dir.rglob("*") if path.is_file()}
    actual_directories = {
        path.resolve() for path in review_dir.rglob("*") if path.is_dir()
    }
    expected_directories = {path.parent for path in expected_files} | {
        review_dir.resolve()
    }
    if actual_files != expected_files or not actual_directories <= expected_directories:
        raise RuntimeError(
            "missing or invalid review directory; it must exactly match selected corpus"
        )
    for directory in directories:
        content = (directory / "content.txt").read_bytes()
        path = review_dir / _relative_case(directory, corpus) / "expected_evidence.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"missing or invalid reviewed obligations: {path}"
            ) from exc
        _validate_reviewed_obligations(payload, directory, content, path)
    return 0


def _selected_directories(corpus: Path, case_ids: tuple[str, ...]) -> list[Path]:
    directories = _posting_directories(corpus)
    if not directories:
        raise RuntimeError(f"posting corpus is unavailable: {corpus}")
    selected = tuple(dict.fromkeys(case_ids))
    if selected:
        known = {directory.name: directory for directory in directories}
        missing = [case_id for case_id in selected if case_id not in known]
        if missing:
            raise RuntimeError(f"unknown case ids: {', '.join(missing)}")
        directories = [known[case_id] for case_id in selected]
    relative_paths = [_relative_case(directory, corpus) for directory in directories]
    if len(set(relative_paths)) != len(relative_paths):
        raise RuntimeError("duplicate corpus paths")
    if len({directory.name for directory in directories}) != len(directories):
        raise RuntimeError("duplicate case ids")
    return directories


async def _draft(args: argparse.Namespace) -> int:
    corpus = args.corpus.resolve()
    output_root = _require_output_path(args.output_root, "draft output")
    directories = _selected_directories(corpus, tuple(args.case or ()))
    statuses: dict[str, str] = {}
    if args.leaf == "eligibility":
        for directory in directories:
            relative = _relative_case(directory, corpus).as_posix()
            statuses[relative] = _draft_eligibility(directory, output_root, corpus)
        contract: dict[str, object] = {"eligibility_version": ELIGIBILITY_VERSION}
        model: str | None = None
    else:
        recordings_root = _require_output_path(args.recordings_dir, "recording output")
        directories = _extractor_directories(directories)
        if not args.model:
            raise RuntimeError("--model is required when drafting extractor output")
        caller = LiteLLMCaller(args.model, timeout=args.timeout)
        for directory in directories:
            text = (directory / "content.txt").read_text(encoding="utf-8")
            relative = _relative_case(directory, corpus).as_posix()
            try:
                await _draft_extractor(
                    directory,
                    caller,
                    args.model,
                    text,
                    output_root,
                    recordings_root,
                    corpus,
                )
                statuses[relative] = "grounded"
            except (ModelCallError, ModelResponseError, ValidationError, ValueError):
                statuses[relative] = "failed"
        contract = {"extractor_prompt_hash": EXTRACTOR_PROMPT_HASH}
        model = args.model
    manifest = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "leaf": args.leaf,
        "case_count": len(directories),
        "source_hashes": {
            _relative_case(directory, corpus).as_posix(): _sha256(
                (directory / "content.txt").read_bytes()
            )
            for directory in directories
        },
        "model": model,
        "contract": contract,
        "reviewed_count": 0,
        "statuses": statuses,
    }
    path = _output_child(output_root, "manifest.json")
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("existing manifest is malformed") from exc
        comparable = {**manifest, "statuses": None}
        if {**existing, "statuses": None} != comparable:
            raise RuntimeError("existing manifest is incompatible")
        return int(any(status == "failed" for status in statuses.values()))
    _atomic_json(path, manifest, _reserve(path))
    return int(any(status == "failed" for status in statuses.values()))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Draft unreviewed expectations under the ignored output directory."
    )
    parser.add_argument(
        "--leaf", choices=("eligibility", "extractor"), default="eligibility"
    )
    parser.add_argument("--model", help="required only for --leaf extractor")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--recordings-dir", type=Path, default=DEFAULT_RECORDINGS)
    parser.add_argument("--case", action="append", default=None, metavar="ID")
    parser.add_argument("--validate-review-dir", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.validate_review_dir is not None:
            return validate_review_dir(
                args.corpus,
                args.validate_review_dir,
                tuple(dict.fromkeys(args.case or ())),
            )
        return asyncio.run(_draft(args))
    except RuntimeError as exc:
        print(f"draft input or output is invalid: {exc}")
        return 2
    except (OSError, ValidationError):
        print("draft input or output is invalid")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
