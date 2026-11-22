from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypedDict

from job_pipeline.content.model import AcquiredContent, AcquisitionFailure
from job_pipeline.runner.diagnostics import redact_url

CaptureContext = Literal["http-only", "browser-fallback", "forced-render"]

_SECRET_PATTERNS = (
    re.compile(
        r"(?i)(?:access[_-]?token|api[_-]?key|client[_-]?secret|password)\s*[=:]\s*[^\s\"'&<>]+"
    ),
    re.compile(
        r"(?i)(?:[?&](?:token|access_token|api_key|key|secret)=)(?!REDACTED(?:[&#]|$))[^&#\s\"'<>]+"
    ),
    re.compile(
        r"(?i)(?:authorization\s*[=:]\s*(?:bearer|basic)\s+)[A-Za-z0-9._~+/-]+=*"
    ),
    re.compile(r"(?i)(?:set-cookie|cookie)\s*:"),
    re.compile(
        r"(?i)[\"'](?:access[_-]?token|api[_-]?key|client[_-]?secret|password|"
        r"authorization|cookie|set-cookie)[\"']\s*:\s*[\"'][^\"']+"
    ),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
)

_MESSAGES = {
    "secret_refusal": "possible secret detected; capture was not published",
    "destination_conflict": "capture destination or publication lock already exists",
    "publication_io": "capture publication failed due to an I/O error",
    "invalid_metadata": "capture metadata is invalid",
}


class CapturePublicationError(Exception):
    category: str

    def __init__(self, category: str) -> None:
        self.category = category if category in _MESSAGES else "publication_io"
        super().__init__(_MESSAGES[self.category])


class CaptureMetadata(TypedDict):
    company_slug: str
    context: CaptureContext
    source_url: str
    resolved_url: str | None
    captured_at: str
    status: Literal["success", "failure"]
    method: str | None
    http_status: int | None
    title: str | None
    retrieved_at: str | None
    content_hash: str | None
    file_digests: dict[str, str]
    frame: bool
    failure_category: str | None
    attempts: list[dict[str, object]]


class CaptureReview(TypedDict):
    status: Literal["pending"]
    checks: dict[str, bool | None]
    location_absent_at_source: bool | None
    omissions: list[str]
    required_excerpts: list[str]
    forbidden_excerpts: list[str]
    reviewer: str | None
    review_date: str | None
    content_hash: str
    file_digests: dict[str, str]


def secret_findings(*values: str) -> list[str]:
    return [
        pattern.pattern
        for value in values
        for pattern in _SECRET_PATTERNS
        if pattern.search(value) or pattern.search(value.replace(r"\"", '"'))
    ]


def _string_values(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _string_values(key)
            yield from _string_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _string_values(item)


def _json(value: object) -> bytes:
    if secret_findings(*_string_values(value)):
        raise CapturePublicationError("secret_refusal")
    return (
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False, ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _safe_value(value: object, key: str = "") -> object:
    if isinstance(value, str):
        return redact_url(value) if key == "url" or key.endswith("_url") else value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    if isinstance(value, dict):
        safe: dict[str, object] = {}
        for name, item in value.items():
            if not isinstance(name, str):
                raise CapturePublicationError("invalid_metadata")
            if name.casefold() in {
                "message",
                "error",
                "exception",
                "body",
                "html",
                "text",
                "reason",
            }:
                continue
            safe[name] = _safe_value(item, name)
        return safe
    raise CapturePublicationError("invalid_metadata")


def _review(result: AcquiredContent, digests: dict[str, str]) -> CaptureReview:
    return {
        "status": "pending",
        "checks": {"title": None, "description": None, "location": None},
        "location_absent_at_source": None,
        "omissions": [],
        "required_excerpts": [],
        "forbidden_excerpts": [],
        "reviewer": None,
        "review_date": None,
        "content_hash": result.content_hash,
        "file_digests": dict(digests),
    }


def _payloads(
    result: AcquiredContent | AcquisitionFailure,
    company_slug: str,
    source_url: str,
    context: str,
    attempts: list[dict[str, object]],
) -> dict[str, bytes]:
    if context not in {"http-only", "browser-fallback", "forced-render"}:
        raise CapturePublicationError("invalid_metadata")
    typed_context: CaptureContext
    if context == "http-only":
        typed_context = "http-only"
    elif context == "browser-fallback":
        typed_context = "browser-fallback"
    else:
        typed_context = "forced-render"
    safe_attempts: list[dict[str, object]] = []
    for attempt in attempts:
        safe = _safe_value(attempt)
        if not isinstance(safe, dict):
            raise CapturePublicationError("invalid_metadata")
        safe_attempts.append(safe)
    payloads: dict[str, bytes] = {}
    success = isinstance(result, AcquiredContent)
    if isinstance(result, AcquiredContent):
        payloads["page.html"] = result.acquisition_html.encode("utf-8")
        if result.frame_html:
            payloads["frame.html"] = result.frame_html.encode("utf-8")
        payloads["content.txt"] = (result.text + "\n").encode("utf-8")
    digests = {
        name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()
    }
    metadata: CaptureMetadata = {
        "company_slug": company_slug,
        "context": typed_context,
        "source_url": redact_url(source_url),
        "resolved_url": redact_url(result.resolved_url)
        if result.resolved_url
        else None,
        "captured_at": datetime.now(UTC).isoformat(),
        "status": "success" if success else "failure",
        "method": result.method if isinstance(result, AcquiredContent) else None,
        "http_status": result.http_status,
        "title": result.title if isinstance(result, AcquiredContent) else None,
        "retrieved_at": (
            result.retrieved_at.isoformat()
            if isinstance(result, AcquiredContent)
            else None
        ),
        "content_hash": (
            result.content_hash if isinstance(result, AcquiredContent) else None
        ),
        "file_digests": dict(digests),
        "frame": bool(result.frame_html)
        if isinstance(result, AcquiredContent)
        else False,
        "failure_category": result.category
        if isinstance(result, AcquisitionFailure)
        else None,
        "attempts": safe_attempts,
    }
    payloads["meta.json"] = _json(metadata)
    if isinstance(result, AcquiredContent):
        digests["meta.json"] = hashlib.sha256(payloads["meta.json"]).hexdigest()
        payloads["review.json"] = _json(_review(result, digests))
    if secret_findings(*(data.decode("utf-8") for data in payloads.values())):
        raise CapturePublicationError("secret_refusal")
    return payloads


def _check_destination(output: Path) -> None:
    if os.path.lexists(output):
        raise CapturePublicationError("destination_conflict")


@contextmanager
def _publication_lock(output: Path) -> Iterator[None]:
    output.parent.mkdir(parents=True, exist_ok=True)
    _check_destination(output)
    lock = output.with_name(f".{output.name}.lock")
    try:
        handle = lock.open("x", encoding="utf-8")
    except FileExistsError:
        raise CapturePublicationError("destination_conflict") from None
    try:
        with handle:
            _check_destination(output)
            yield
    finally:
        lock.unlink()


def preflight_output(output: Path) -> None:
    try:
        with _publication_lock(output):
            temporary = Path(
                tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent)
            )
            shutil.rmtree(temporary)
    except OSError:
        raise CapturePublicationError("publication_io") from None


def publish_capture(
    output: Path,
    *,
    result: AcquiredContent | AcquisitionFailure,
    company_slug: str,
    source_url: str,
    context: str,
    attempts: list[dict[str, object]],
) -> None:
    try:
        payloads = _payloads(result, company_slug, source_url, context, attempts)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise CapturePublicationError("invalid_metadata") from None
    temporary: Path | None = None
    try:
        with _publication_lock(output):
            try:
                temporary = Path(
                    tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent)
                )
                for name, data in payloads.items():
                    (temporary / name).write_bytes(data)
                _check_destination(output)
                temporary.rename(output)
                temporary = None
            finally:
                if temporary is not None:
                    shutil.rmtree(temporary)
    except OSError:
        raise CapturePublicationError("publication_io") from None
