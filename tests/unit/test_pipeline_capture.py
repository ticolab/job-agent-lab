from __future__ import annotations

import asyncio
import hashlib
import html
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from job_pipeline.content.model import AcquiredContent, AcquisitionFailure, hash_content
from job_pipeline.runner import capture
from job_pipeline.runner.capture import (
    CapturePublicationError,
    preflight_output,
    publish_capture,
)


@pytest.fixture
def content() -> AcquiredContent:
    text = "Senior Engineer\nBuild reliable APIs in Costa Rica."
    return AcquiredContent(
        text=text,
        title="Senior Engineer",
        resolved_url="https://jobs.example.com/1",
        http_status=200,
        method="main_content",
        requisition_id="REQ-1",
        jsonld=None,
        retrieved_at=datetime(2026, 11, 1, tzinfo=UTC),
        content_hash=hash_content(text),
        acquisition_html="<html><body>Senior Engineer</body></html>",
    )


def _publish(
    output: Path,
    result: AcquiredContent | AcquisitionFailure,
    *,
    attempts: list[dict[str, object]] | None = None,
    context: str = "http-only",
    source_url: str = "https://jobs.example.com/1",
) -> None:
    publish_capture(
        output,
        result=result,
        company_slug="example",
        source_url=source_url,
        context=context,
        attempts=[] if attempts is None else attempts,
    )


@pytest.mark.parametrize("frame", [False, True])
@pytest.mark.parametrize("context", ["http-only", "browser-fallback", "forced-render"])
def test_success_schema_and_exact_hashes(
    tmp_path: Path, content: AcquiredContent, frame: bool, context: str
) -> None:
    if frame:
        content = replace(
            content, frame_html="<body>frame</body>", method="rendered_frame_main"
        )
    output = tmp_path / "nested" / "posting"
    _publish(output, content, context=context)
    expected = {"page.html", "content.txt", "meta.json", "review.json"}
    if frame:
        expected.add("frame.html")
    assert {path.name for path in output.iterdir()} == expected
    assert (output / "page.html").read_bytes() == content.acquisition_html.encode()
    assert (output / "content.txt").read_bytes() == (content.text + "\n").encode()
    if frame:
        assert (output / "frame.html").read_bytes() == content.frame_html.encode()
    metadata = json.loads((output / "meta.json").read_bytes())
    assert set(metadata) == {
        "company_slug",
        "context",
        "source_url",
        "resolved_url",
        "captured_at",
        "status",
        "method",
        "http_status",
        "title",
        "retrieved_at",
        "content_hash",
        "file_digests",
        "frame",
        "failure_category",
        "attempts",
    }
    assert metadata["context"] == context
    assert metadata["company_slug"] == "example"
    assert metadata["status"] == "success"
    assert metadata["content_hash"] == content.content_hash
    assert metadata["title"] == content.title
    assert metadata["method"] == content.method
    assert metadata["http_status"] == 200
    assert metadata["retrieved_at"] == content.retrieved_at.isoformat()
    assert metadata["frame"] is frame
    assert metadata["failure_category"] is None
    offset = datetime.fromisoformat(metadata["captured_at"]).utcoffset()
    assert offset is not None and offset.total_seconds() == 0
    review = json.loads((output / "review.json").read_bytes())
    assert review == {
        "status": "pending",
        "checks": {"title": None, "description": None, "location": None},
        "location_absent_at_source": None,
        "omissions": [],
        "required_excerpts": [],
        "forbidden_excerpts": [],
        "reviewer": None,
        "review_date": None,
        "content_hash": content.content_hash,
        "file_digests": {
            name: hashlib.sha256((output / name).read_bytes()).hexdigest()
            for name in expected - {"review.json"}
        },
    }
    assert metadata["file_digests"] == {
        name: digest
        for name, digest in review["file_digests"].items()
        if name != "meta.json"
    }
    assert {p.name for p in output.parent.iterdir()} == {"posting"}


@pytest.mark.parametrize(
    "resolved",
    [None, "not a url", "https://user:secret@jobs.example.com/1?key=PRIVATE#PRIVATE"],
)
def test_failure_metadata_only_without_message(
    tmp_path: Path, resolved: str | None
) -> None:
    output = tmp_path / "failure"
    _publish(output, AcquisitionFailure("blocked", "password=PRIVATE", 403, resolved))
    assert {p.name for p in output.iterdir()} == {"meta.json"}
    data = (output / "meta.json").read_text()
    assert "PRIVATE" not in data
    metadata = json.loads(data)
    assert metadata["status"] == "failure"
    assert metadata["failure_category"] == "blocked"
    assert metadata["http_status"] == 403
    for key in ("title", "method", "content_hash", "retrieved_at"):
        assert metadata[key] is None
    assert metadata["file_digests"] == {}
    assert metadata["frame"] is False
    assert "message" not in metadata


def test_urls_and_invocations_are_safe_without_mutating_caller(
    tmp_path: Path, content: AcquiredContent
) -> None:
    url = "https://user:PRIVATE@jobs.example.com:443/1?token=PRIVATE#PRIVATE"
    attempts: list[dict[str, object]] = [
        {
            "resolved_url": url,
            "steps": [{"url": url, "http_status": 200}],
            "message": "PRIVATE raw failure",
            "error": "PRIVATE raw failure",
            "reason": "PRIVATE raw failure",
            "elapsed_s": 0.5,
            "force_rendered": False,
        }
    ]
    output = tmp_path / "posting"
    _publish(
        output, replace(content, resolved_url=url), source_url=url, attempts=attempts
    )
    data = (output / "meta.json").read_text()
    assert "PRIVATE" not in data
    metadata = json.loads(data)
    assert metadata["source_url"] == "https://jobs.example.com:443/1"
    assert metadata["resolved_url"] == metadata["source_url"]
    assert metadata["attempts"] == [
        {
            "resolved_url": metadata["source_url"],
            "steps": [{"url": metadata["source_url"], "http_status": 200}],
            "elapsed_s": 0.5,
            "force_rendered": False,
        }
    ]
    assert attempts[0]["resolved_url"] == url
    assert "message" in attempts[0]


@pytest.mark.parametrize(
    "secret",
    [
        "api_key=PRIVATE",
        "Authorization: Bearer abc.def.ghi",
        "Authorization:\nBearer abc.def.ghi",
        "Authorization:\tBearer abc.def.ghi",
        "api_key=\nPRIVATE",
        "api_key=\tPRIVATE",
        "Set-Cookie: session=PRIVATE",
        '<a href="https://jobs.example.com/1?token=PRIVATE">job</a>',
        '"api_key": "PRIVATE"',
        "sk-abcdefghijklmnopqrstuvwx",
    ],
)
@pytest.mark.parametrize(
    "payload", ["page", "frame", "content", "meta", "review", "attempt", "failure"]
)
def test_every_payload_scanned_before_writing(
    tmp_path: Path,
    content: AcquiredContent,
    monkeypatch: pytest.MonkeyPatch,
    secret: str,
    payload: str,
) -> None:
    output = tmp_path / "posting"
    result: AcquiredContent | AcquisitionFailure = content
    attempts: list[dict[str, object]] = []
    if payload == "page":
        result = replace(content, acquisition_html=secret)
    elif payload == "frame":
        result = replace(content, frame_html=secret)
    elif payload == "content":
        result = replace(content, text=secret)
    elif payload == "meta":
        result = replace(content, title=secret)
    elif payload == "review":
        original = capture._review

        def poisoned(
            result: AcquiredContent, digests: dict[str, str]
        ) -> capture.CaptureReview:
            review = original(result, digests)
            review["required_excerpts"] = [secret]
            return review

        monkeypatch.setattr(capture, "_review", poisoned)
    else:
        attempts = [{"diagnostic": secret}]
        if payload == "failure":
            result = AcquisitionFailure("transport", "safe")
    with pytest.raises(CapturePublicationError) as caught:
        _publish(output, result, attempts=attempts)
    assert caught.value.category == "secret_refusal"
    assert secret not in str(caught.value)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "secret",
    [
        "Authorization:\nBearer abc.def.ghi",
        "Authorization:\tBearer abc.def.ghi",
        "api_key=\nPRIVATE",
        "api_key=\tPRIVATE",
    ],
)
@pytest.mark.parametrize("in_key", [False, True])
@pytest.mark.parametrize("failed", [False, True])
def test_nested_attempt_strings_scanned_before_json_escaping(
    tmp_path: Path, content: AcquiredContent, secret: str, in_key: bool, failed: bool
) -> None:
    nested = {secret: "safe"} if in_key else {"label": secret}
    attempts: list[dict[str, object]] = [{"steps": [{"details": [nested]}]}]
    result = AcquisitionFailure("transport", "safe") if failed else content
    with pytest.raises(CapturePublicationError) as caught:
        _publish(tmp_path / "posting", result, attempts=attempts)
    assert caught.value.category == "secret_refusal"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("separator", ["&#10;", "&#9;"])
def test_entity_decoded_title_scanned_before_json_escaping(
    tmp_path: Path, content: AcquiredContent, separator: str
) -> None:
    encoded_title = f"Authorization:{separator}Bearer abc.def.ghi"
    page = f"<html><title>{encoded_title}</title></html>"
    assert not capture.secret_findings(page)
    title = html.unescape(encoded_title)
    assert capture.secret_findings(title)
    with pytest.raises(CapturePublicationError) as caught:
        _publish(
            tmp_path / "posting", replace(content, acquisition_html=page, title=title)
        )
    assert caught.value.category == "secret_refusal"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "kind", ["directory", "file", "symlink", "dangling", "lock", "lock_symlink"]
)
@pytest.mark.parametrize("preflight", [False, True])
def test_existing_destinations_and_locks_are_never_replaced(
    tmp_path: Path, content: AcquiredContent, kind: str, preflight: bool
) -> None:
    output = tmp_path / "posting"
    target = tmp_path / "target"
    target.write_text("keep")
    lock = tmp_path / ".posting.lock"
    if kind == "directory":
        output.mkdir()
    elif kind == "file":
        output.write_text("keep")
    elif kind == "symlink":
        output.symlink_to(target)
    elif kind == "dangling":
        output.symlink_to(tmp_path / "absent")
    elif kind == "lock":
        lock.write_text("owned by another writer")
    else:
        lock.symlink_to(tmp_path / "absent")
    before = {p.name for p in tmp_path.iterdir()}
    with pytest.raises(CapturePublicationError) as caught:
        if preflight:
            preflight_output(output)
        else:
            _publish(output, content)
    assert caught.value.category == "destination_conflict"
    assert {p.name for p in tmp_path.iterdir()} == before
    assert target.read_text() == "keep"
    if kind in {"symlink", "dangling"}:
        assert output.is_symlink()


def test_preflight_creates_only_ancestors(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "posting"
    preflight_output(output)
    assert not output.exists()
    assert list(output.parent.iterdir()) == []


def test_preflight_rejects_file_parent(tmp_path: Path) -> None:
    parent = tmp_path / "file"
    parent.write_text("keep")
    with pytest.raises(CapturePublicationError) as caught:
        preflight_output(parent / "posting")
    assert caught.value.category == "publication_io"
    assert parent.read_text() == "keep"


@pytest.mark.parametrize("stage", ["lock", "temporary", "write", "rename"])
@pytest.mark.parametrize("interruption", ["io", "keyboard", "cancelled"])
def test_faults_cleanup_without_partial_publication(
    tmp_path: Path,
    content: AcquiredContent,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    interruption: str,
) -> None:
    output = tmp_path / "posting"
    error: BaseException
    if interruption == "keyboard":
        error = KeyboardInterrupt()
    elif interruption == "cancelled":
        error = asyncio.CancelledError()
    else:
        error = PermissionError("password=PRIVATE")

    def fail(*args: object, **kwargs: object) -> object:
        raise error

    if stage == "lock":
        monkeypatch.setattr(Path, "open", fail)
    elif stage == "temporary":
        monkeypatch.setattr(capture.tempfile, "mkdtemp", fail)
    elif stage == "write":
        original_write = Path.write_bytes
        count = 0

        def write(path: Path, data: bytes) -> int:
            nonlocal count
            count += 1
            if count == 2:
                raise error
            return original_write(path, data)

        monkeypatch.setattr(Path, "write_bytes", write)
    else:
        monkeypatch.setattr(Path, "rename", fail)
    if interruption != "io":
        with pytest.raises(type(error)):
            _publish(output, content)
    else:
        with pytest.raises(CapturePublicationError) as caught:
            _publish(output, content)
        assert caught.value.category == "publication_io"
        assert "PRIVATE" not in str(caught.value)
    assert not list(tmp_path.iterdir())


def test_final_destination_check_refuses_late_directory(
    tmp_path: Path, content: AcquiredContent, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "posting"
    original = Path.write_bytes

    def write(path: Path, data: bytes) -> int:
        count = original(path, data)
        if path.name == "review.json":
            output.mkdir()
            (output / "keep").write_text("another writer")
        return count

    monkeypatch.setattr(Path, "write_bytes", write)
    with pytest.raises(CapturePublicationError) as caught:
        _publish(output, content)
    assert caught.value.category == "destination_conflict"
    assert (output / "keep").read_text() == "another writer"
    assert {p.name for p in tmp_path.iterdir()} == {"posting"}


def test_concurrent_cooperating_writers_have_one_winner(
    tmp_path: Path, content: AcquiredContent, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "posting"
    writing = threading.Event()
    release = threading.Event()
    original = Path.write_bytes

    def pause(path: Path, data: bytes) -> int:
        if path.name == "page.html":
            writing.set()
            assert release.wait(timeout=10)
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(_publish, output, content)
        try:
            assert writing.wait(timeout=10)
            with pytest.raises(CapturePublicationError) as caught:
                _publish(output, replace(content, text="loser"))
            assert caught.value.category == "destination_conflict"
            assert not output.exists()
        finally:
            release.set()
        first.result(timeout=10)
    assert (output / "content.txt").read_text() == content.text + "\n"
    assert {p.name for p in tmp_path.iterdir()} == {"posting"}


@pytest.mark.parametrize(
    "context,attempts",
    [
        ("invalid", []),
        ("http-only", [{"elapsed_s": float("nan")}]),
        ("http-only", [{"unsupported": object()}]),
        ("http-only", [{"diagnostic": "\ud800"}]),
    ],
)
def test_invalid_metadata_is_typed_and_creates_nothing(
    tmp_path: Path,
    content: AcquiredContent,
    context: str,
    attempts: list[dict[str, object]],
) -> None:
    with pytest.raises(CapturePublicationError) as caught:
        _publish(tmp_path / "posting", content, context=context, attempts=attempts)
    assert caught.value.category == "invalid_metadata"
    assert not list(tmp_path.iterdir())


def test_preflight_permission_error_is_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> object:
        raise PermissionError("password=PRIVATE")

    monkeypatch.setattr(capture.tempfile, "mkdtemp", fail)
    with pytest.raises(CapturePublicationError) as caught:
        preflight_output(tmp_path / "posting")
    assert caught.value.category == "publication_io"
    assert "PRIVATE" not in str(caught.value)
    assert not list(tmp_path.iterdir())
