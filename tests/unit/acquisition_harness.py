from __future__ import annotations

import hashlib
import json
import socket
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Never, Self, cast

import httpx
import pytest

from job_pipeline.content import jsonld
from job_pipeline.content.fetch import FetchResult
from job_pipeline.content.ladder import _extract, _extract_rendered
from job_pipeline.content.model import AcquiredContent, AcquisitionFailure, hash_content
from job_pipeline.content.render import FrameCapture, RenderedPage
from job_pipeline.runner.capture import publish_capture

CAPTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "acquisition"
SYNTHETIC_URL = "https://synthetic.invalid/posting/selection-test"
SYNTHETIC_DATE = "2026-06-15T12:00:00+00:00"
REQUIRED = "Maintain synthetic observatories with reproducible measurements."
FORBIDDEN = "Synthetic forbidden recruitment banner."
DESCRIPTION = " ".join(
    [REQUIRED, "Location: Synthetic Valley."]
    + [
        f"Synthetic responsibility {index}: verify instruments carefully."
        for index in range(14)
    ]
)


@dataclass(frozen=True)
class ReviewedCapture:
    path: Path
    metadata: dict[str, object]
    review: dict[str, object]
    text: str


def read_record(path: Path) -> dict[str, object]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict), f"{path.name}: expected object"
    assert all(isinstance(key, str) for key in value)
    return cast(dict[str, object], value)


def write_record(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def record(value: object, label: str) -> dict[str, object]:
    assert isinstance(value, dict), f"{label}: expected object"
    assert all(isinstance(key, str) for key in value), label
    return cast(dict[str, object], value)


def nonempty(value: object, label: str) -> str:
    assert isinstance(value, str) and value.strip(), label
    return value


def strings(value: object, label: str) -> list[str]:
    assert isinstance(value, list), f"{label}: expected list"
    return [nonempty(item, label) for item in value]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def approved_captures(
    root: Path = CAPTURE_ROOT, *, include_automated: bool = False
) -> tuple[Path, ...]:
    approved: list[Path] = []
    filenames = {
        "page.html",
        "frame.html",
        "content.txt",
        "meta.json",
        "review.json",
        "validation.json",
        "reference.json",
    }
    bundles: set[Path] = set()
    for path in root.rglob("*"):
        if path.name not in filenames:
            continue
        assert len(path.relative_to(root).parts) == 3, (
            f"capture file must be at <slug>/<case>/<filename>: {path}"
        )
        assert path.is_file(), f"capture artifact must be a file: {path}"
        bundles.add(path.parent)
    for path in sorted(bundles):
        assert (path / "meta.json").is_file(), f"missing meta.json: {path}"
        metadata = read_record(path / "meta.json")
        assert metadata.get("status") == "success", (
            f"only successful candidate bundles belong in acquisition fixtures: {path}"
        )
        for name in ("page.html", "content.txt"):
            assert (path / name).is_file(), f"missing {name}: {path}"
        automated = (path / "reference.json").exists() or (
            path / "validation.json"
        ).exists()
        if automated:
            assert (path / "validation.json").is_file(), (
                f"missing validation.json: {path}"
            )
            assert (path / "reference.json").is_file(), (
                f"missing reference.json: {path}"
            )
            validate_automated(path)
            if include_automated:
                approved.append(path)
            continue
        assert (path / "review.json").is_file(), f"missing review.json: {path}"
        review = read_record(path / "review.json")
        status = review.get("status")
        assert status in {"pending", "rejected", "blocked", "approved"}, path
        if status == "approved":
            approved.append(path)
    return tuple(approved)


def validate_review(path: Path) -> ReviewedCapture:
    metadata = read_record(path / "meta.json")
    review = read_record(path / "review.json")
    assert review.get("status") == "approved", "review is not approved"
    assert metadata.get("status") == "success", "capture is not successful"
    nonempty(review.get("reviewer"), "reviewer")
    reviewed_at = datetime.fromisoformat(
        nonempty(review.get("review_date"), "review_date")
    )
    assert reviewed_at.date().isoformat() == str(review["review_date"])[:10], (
        "review_date"
    )
    checks = record(review.get("checks"), "checks")
    assert checks.get("title") is True, "title check"
    assert checks.get("description") is True, "description check"
    assert type(checks.get("location")) is bool, "location check"
    absent = review.get("location_absent_at_source")
    assert type(absent) is bool, "location_absent_at_source"
    assert checks["location"] or absent, "location must be captured or absent at source"
    strings(review.get("omissions"), "omissions")
    required = strings(review.get("required_excerpts"), "required_excerpts")
    forbidden = strings(review.get("forbidden_excerpts"), "forbidden_excerpts")
    assert required, "required_excerpts must not be empty"
    text = (path / "content.txt").read_text(encoding="utf-8")
    for excerpt in required:
        assert excerpt in text, f"required excerpt missing: {excerpt}"
    for excerpt in forbidden:
        assert excerpt not in text, f"forbidden excerpt present: {excerpt}"
    validate_integrity(path, metadata, review, text, "review")
    return ReviewedCapture(path, metadata, review, text)


def validate_integrity(
    path: Path,
    metadata: dict[str, object],
    review: dict[str, object],
    text: str,
    label: str,
) -> None:
    assert metadata.get("status") == "success", "capture is not successful"
    content_hash = hash_content(text)
    assert metadata.get("content_hash") == content_hash, "metadata content_hash"
    assert review.get("content_hash") == content_hash, f"{label} content_hash"
    assert type(metadata.get("frame")) is bool, "frame flag"
    files = {"page.html", "content.txt"}
    if metadata["frame"]:
        files.add("frame.html")
    assert (path / "frame.html").exists() == metadata["frame"], "frame file mismatch"
    metadata_digests = record(metadata.get("file_digests"), "metadata file_digests")
    review_digests = record(review.get("file_digests"), "review file_digests")
    assert set(metadata_digests) == files, "metadata file_digests members"
    bound_files = files | {"meta.json"}
    if label == "validation":
        bound_files.add("reference.json")
    assert set(review_digests) == bound_files, f"{label} file_digests members"
    for name in sorted(files):
        actual = digest(path / name)
        assert metadata_digests[name] == actual, f"metadata digest: {name}"
        assert review_digests[name] == actual, f"{label} digest: {name}"
    for name in sorted(bound_files - files):
        assert review_digests[name] == digest(path / name), f"{label} digest: {name}"
    assert metadata.get("context") in {
        "http-only",
        "browser-fallback",
        "forced-render",
    }, "context"
    nonempty(metadata.get("title"), "metadata title")
    nonempty(metadata.get("method"), "metadata method")
    assert type(metadata.get("http_status")) is int, "http_status"


@dataclass(frozen=True)
class AutomatedCapture:
    path: Path
    metadata: dict[str, object]
    validation: dict[str, object]
    text: str
    required_excerpts: tuple[str, ...]


def normalize_completeness(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


COMPLETENESS_CRITERION = "full_reference_passages_unicode_nfkc_whitespace_any_order"


def reference_excerpts(reference: dict[str, object]) -> tuple[str, ...]:
    snapshot = record(reference.get("snapshot"), "reference snapshot")
    title = nonempty(snapshot.get("title"), "reference title")
    description = nonempty(snapshot.get("description"), "reference description")
    details = snapshot.get("details_text")
    assert isinstance(details, str), "reference details_text"
    absent = snapshot.get("source_location_absent", False)
    assert type(absent) is bool, "reference source_location_absent"
    location = snapshot.get("location_text")
    if absent:
        assert location is None or location == "", "absent reference location conflicts"
    elif "location_text" in snapshot:
        location = nonempty(location, "reference location_text")
    else:
        lines = [line.strip() for line in details.splitlines() if line.strip()]
        assert "Locations" in lines, "reference location"
        index = lines.index("Locations") + 1
        assert index < len(lines), "reference location"
        location = nonempty(lines[index], "reference location")
    excerpts = (
        title,
        *(
            line.strip()
            for text in (description, details)
            for line in text.splitlines()
            if line.strip()
        ),
    )
    if (
        not absent
        and isinstance(location, str)
        and normalize_completeness(location)
        not in {normalize_completeness(excerpt) for excerpt in excerpts}
    ):
        excerpts += (location,)
    return excerpts


def assert_complete(text: str, excerpts: tuple[str, ...]) -> None:
    normalized = normalize_completeness(text)
    for excerpt in excerpts:
        assert normalize_completeness(excerpt) in normalized, (
            f"required reference passage missing: {excerpt}"
        )


def validate_automated(path: Path) -> AutomatedCapture:
    metadata = read_record(path / "meta.json")
    validation = read_record(path / "validation.json")
    assert validation.get("status") == "success", "validation status"
    assert validation.get("validation_type") == "automated_browser_comparison", (
        "validation_type"
    )
    assert validation.get("human_approval") is False, "human_approval"
    assert "reviewer" not in validation and "review_date" not in validation, (
        "automated validation cannot claim human review"
    )
    if (path / "review.json").exists():
        review = read_record(path / "review.json")
        assert review.get("status") == "pending", "conflicting human review"
        assert review.get("reviewer") is None and review.get("review_date") is None, (
            "conflicting human review"
        )
    assert validation.get("completeness_criterion") == COMPLETENESS_CRITERION, (
        "completeness_criterion"
    )
    checks = record(validation.get("checks"), "validation checks")
    assert set(checks) == {"title", "description", "location", "capture_integrity"}, (
        "validation checks members"
    )
    for name, value in checks.items():
        assert value is True, f"validation check: {name}"
    assert validation.get("omissions") == [], "validation omissions"
    assert type(validation.get("source_location_absent")) is bool, (
        "source_location_absent"
    )
    validated = datetime.fromisoformat(
        nonempty(validation.get("validated_at"), "validated_at")
    )
    assert validated.tzinfo is not None, "validated_at timezone"
    text = (path / "content.txt").read_text(encoding="utf-8")
    validate_integrity(path, metadata, validation, text, "validation")
    reference = read_record(path / "reference.json")
    assert (
        reference.get("selected_url")
        == validation.get("selected_url")
        == metadata.get("source_url")
    ), "selected_url"
    assert reference.get("resolved_url") == metadata.get("resolved_url"), "resolved_url"
    assert validation.get("acquisition_context") == metadata.get("context"), (
        "acquisition_context"
    )
    excerpts = reference_excerpts(reference)
    required = strings(validation.get("required_excerpts"), "required_excerpts")
    assert required == list(excerpts), "required_excerpts must cover complete reference"
    snapshot = record(reference["snapshot"], "snapshot")
    assert validation["source_location_absent"] is snapshot.get(
        "source_location_absent", False
    ), "source_location_absent must match reference"
    assert normalize_completeness(
        nonempty(metadata.get("title"), "title")
    ) == normalize_completeness(nonempty(snapshot.get("title"), "reference title")), (
        "reference title"
    )
    assert_complete(text, excerpts)
    return AutomatedCapture(path, metadata, validation, text, excerpts)


def validate_capture(path: Path) -> ReviewedCapture | AutomatedCapture:
    if (path / "validation.json").exists() or (path / "reference.json").exists():
        return validate_automated(path)
    return validate_review(path)


def freeze_selection_date(monkeypatch: pytest.MonkeyPatch, instant: str) -> datetime:
    recorded = datetime.fromisoformat(instant)
    assert recorded.tzinfo is not None, "recorded date must include a timezone"
    recorded = recorded.astimezone(UTC)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            value = recorded.astimezone(tz) if tz else recorded.replace(tzinfo=None)
            return cls.fromisoformat(value.isoformat())

    monkeypatch.setattr(jsonld, "datetime", FrozenDateTime)
    return recorded


def select_capture(
    *,
    html: str,
    frame_html: str,
    method: str,
    url: str,
    status: int,
    retrieved_at: datetime,
) -> AcquiredContent | AcquisitionFailure | None:
    fetched = FetchResult(html, status, url, httpx.Headers())
    if method.startswith("rendered_"):
        frames = (
            (FrameCapture(0, url, "same_origin", "candidate", html=frame_html),)
            if frame_html
            else ()
        )
        return _extract_rendered(
            RenderedPage(html, url, len(frames), frames),
            fetched=fetched,
            retrieved_at=retrieved_at,
        )
    assert not frame_html, "HTTP selection cannot contain a selected frame"
    assert method in {"jsonld", "main_content"}, "unsupported selection method"
    return _extract(html, fetched=fetched, method_prefix="", retrieved_at=retrieved_at)


def replay_selection(
    capture: ReviewedCapture | AutomatedCapture, monkeypatch: pytest.MonkeyPatch
) -> AcquiredContent:
    metadata = capture.metadata
    instant = nonempty(metadata.get("retrieved_at"), "retrieved_at")
    started = datetime.fromisoformat(instant)
    finished = datetime.fromisoformat(
        nonempty(metadata.get("captured_at"), "captured_at")
    )
    assert started.tzinfo is not None and finished.tzinfo is not None, (
        "selection date uncertain: capture timestamps must include timezones"
    )
    started, finished = started.astimezone(UTC), finished.astimezone(UTC)
    assert started <= finished and started.date() == finished.date(), (
        "selection date uncertain: retrieval/publication must bound one UTC day; "
        "retrieved_at precedes selection and is not a recorded evaluation date"
    )
    retrieved_at = freeze_selection_date(monkeypatch, instant)
    status = metadata.get("http_status")
    assert type(status) is int, "http_status"
    result = select_capture(
        html=(capture.path / "page.html").read_text(encoding="utf-8"),
        frame_html=(capture.path / "frame.html").read_text(encoding="utf-8")
        if metadata["frame"]
        else "",
        method=nonempty(metadata.get("method"), "method"),
        url=nonempty(metadata.get("resolved_url") or metadata.get("source_url"), "url"),
        status=status,
        retrieved_at=retrieved_at,
    )
    assert isinstance(result, AcquiredContent), "selection did not reproduce success"
    assert metadata["status"] == "success", "status"
    assert result.title == metadata["title"], "title"
    assert result.method == metadata["method"], "method"
    assert result.http_status == metadata["http_status"], "http_status"
    if isinstance(capture, AutomatedCapture):
        assert_complete(result.text, capture.required_excerpts)
    else:
        assert result.text + "\n" == capture.text, "selection text"
        assert result.content_hash == metadata["content_hash"], "selection content_hash"
    return result


def synthetic_html(
    *,
    structured: bool = True,
    valid_through: str | None = None,
    location_present: bool = True,
) -> str:
    description = (
        DESCRIPTION
        if location_present
        else DESCRIPTION.replace("Location: Synthetic Valley.", "")
    )
    posting: dict[str, object] = {
        "@type": "JobPosting",
        "title": "Synthetic instrument specialist",
        "description": description,
    }
    if valid_through:
        posting["validThrough"] = valid_through
    script = (
        f'<script type="application/ld+json">{json.dumps(posting)}</script>'
        if structured
        else ""
    )
    return (
        f"<html><head><title>Synthetic page title</title>{script}</head>"
        f"<body><main><p>{'' if structured else description}</p></main></body></html>"
    )


def synthetic_capture(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    method: str = "jsonld",
    valid_through: str | None = None,
    location_present: bool = True,
) -> Path:
    html = synthetic_html(
        structured=method.endswith("jsonld"),
        valid_through=valid_through,
        location_present=location_present,
    )
    frame_html = ""
    if method.startswith("rendered_frame_"):
        frame_html = html
        html = (
            "<html><head><title>Synthetic parent title</title></head>"
            "<body>Shell</body></html>"
        )
    retrieved_at = freeze_selection_date(monkeypatch, SYNTHETIC_DATE)
    result = select_capture(
        html=html,
        frame_html=frame_html,
        method=method,
        url=SYNTHETIC_URL,
        status=200,
        retrieved_at=retrieved_at,
    )
    assert isinstance(result, AcquiredContent)
    output = root / "synthetic-only" / method
    with monkeypatch.context() as publication_clock:
        publication_clock.setattr(
            "job_pipeline.runner.capture.datetime", jsonld.datetime
        )
        publish_capture(
            output,
            result=result,
            company_slug="synthetic-only",
            source_url=SYNTHETIC_URL,
            context="forced-render" if method.startswith("rendered_") else "http-only",
            attempts=[],
        )
    return output


def synthetic_review(path: Path) -> None:
    review = read_record(path / "review.json")
    review.update(
        {
            "status": "approved",
            "checks": {"title": True, "description": True, "location": True},
            "location_absent_at_source": False,
            "required_excerpts": [REQUIRED, "Location: Synthetic Valley."],
            "forbidden_excerpts": [FORBIDDEN],
            "reviewer": "SYNTHETIC TEST INPUT, NOT HUMAN APPROVAL",
            "review_date": "2026-06-15",
        }
    )
    write_record(path / "review.json", review)


def block_live_access(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> Never:
        raise AssertionError("live network/model access forbidden in selection replay")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    monkeypatch.setattr("litellm.completion", forbidden)
    monkeypatch.setattr("litellm.acompletion", forbidden)
