from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from job_pipeline.content.fetch import FetchResult
from job_pipeline.content.model import (
    AcquiredContent,
    AcquisitionFailure,
    AcquisitionTrace,
    FrameTrace,
    StepTrace,
    hash_content,
)

ROOT = Path(__file__).resolve().parents[2]


def _load(script: str = "capture_posting") -> ModuleType:
    path = ROOT / "scripts" / f"{script}.py"
    spec = importlib.util.spec_from_file_location(f"{script}_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _content(html: str) -> AcquiredContent:
    text = "Senior Engineer\nBuild reliable APIs."
    return AcquiredContent(
        text=text,
        title="Senior Engineer",
        resolved_url="https://jobs.example.com/1",
        http_status=200,
        method="main_content",
        requisition_id="REQ-1",
        jsonld=None,
        retrieved_at=datetime.now(UTC),
        content_hash=hash_content(text),
        acquisition_html=html,
    )


def test_atomic_fixture_writes_exact_acquisition_html(tmp_path: Path) -> None:
    module = _load()
    html = "<html><body>exact acquisition</body></html>"
    content = _content(html)
    raw = FetchResult(html, 200, content.resolved_url, httpx.Headers())
    output = tmp_path / "posting"
    module._atomic_fixture(output, raw, content, content.resolved_url)
    assert (output / "page.html").read_text(encoding="utf-8") == html
    metadata = json.loads((output / "meta.json").read_text(encoding="utf-8"))
    assert metadata["frame"] is False
    assert sorted(path.name for path in output.iterdir()) == [
        "content.txt",
        "meta.json",
        "page.html",
    ]


@pytest.mark.parametrize(
    "html",
    [
        '<a href="https://jobs.example.com/1?token=secret">job</a>',
        "Authorization: Bearer abc.def.ghi",
        "Set-Cookie: session=secret",
        "api_key=secret",
    ],
)
def test_atomic_fixture_fails_closed_on_possible_secret(
    tmp_path: Path, html: str
) -> None:
    module = _load()
    content = _content(html)
    raw = FetchResult(html, 200, content.resolved_url, httpx.Headers())
    output = tmp_path / "posting"
    with pytest.raises(ValueError, match="possible secret"):
        module._atomic_fixture(output, raw, content, content.resolved_url)
    assert not output.exists()
    assert not list(tmp_path.iterdir())


def test_atomic_fixture_writes_selected_frame(tmp_path: Path) -> None:
    module = _load()
    page = "<html><body>Page chrome</body></html>"
    frame = "<html><body>Selected description</body></html>"
    content = replace(_content(page), frame_html=frame, method="rendered_frame_main")
    raw = FetchResult(page, 200, content.resolved_url, httpx.Headers())
    output = tmp_path / "posting"
    module._atomic_fixture(output, raw, content, content.resolved_url)
    assert (output / "page.html").read_text(encoding="utf-8") == page
    assert (output / "frame.html").read_text(encoding="utf-8") == frame
    assert (output / "content.txt").read_text(encoding="utf-8") == content.text + "\n"
    metadata = json.loads((output / "meta.json").read_text(encoding="utf-8"))
    assert metadata["frame"] is True
    assert metadata["method"] == "rendered_frame_main"
    assert sorted(path.name for path in output.iterdir()) == [
        "content.txt",
        "frame.html",
        "meta.json",
        "page.html",
    ]


@pytest.mark.parametrize(
    "secret",
    [
        '<a href="https://jobs.example.com/1?token=secret">job</a>',
        "Authorization: Bearer abc.def.ghi",
        "Set-Cookie: session=secret",
        "api_key=secret",
    ],
)
def test_frame_secret_refused_before_any_file_write(
    tmp_path: Path, secret: str
) -> None:
    module = _load()
    page = "<html><body>Page chrome</body></html>"
    content = replace(_content(page), frame_html=secret)
    raw = FetchResult(page, 200, content.resolved_url, httpx.Headers())
    with pytest.raises(ValueError, match="possible secret"):
        module._write_fixture(tmp_path, raw, content, content.resolved_url)
    assert not list(tmp_path.iterdir())
    with pytest.raises(ValueError, match="possible secret"):
        module._atomic_fixture(tmp_path / "posting", raw, content, content.resolved_url)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("failed", [False, True])
def test_probe_reports_trace_without_bodies_or_secrets(
    capsys: pytest.CaptureFixture[str], failed: bool
) -> None:
    module = _load("probe_posting")
    url = "https://user:password@jobs.example.com/1?token=QUERY-SECRET#FRAGMENT"
    trace = AcquisitionTrace(
        steps=(
            StepTrace("http", 200, url, 12, False, 0.1),
            StepTrace("render", 200, url, 73, False, 0.2),
        ),
        frames_found=5,
        frames=(
            FrameTrace(0, "about", "selected", 1500, "BODY-MARKER", "BODY-MARKER"),
            FrameTrace(1, "same_origin", "qualifying", 500),
            FrameTrace(2, "about", "short", 30),
            FrameTrace(3, "about", "unreadable", 0),
            FrameTrace(4, "about", "over_limit", 0),
        ),
        selected_frame=0,
    )
    result: AcquiredContent | AcquisitionFailure
    if failed:
        result = AcquisitionFailure(
            "empty_content", "BODY-MARKER api_key=SECRET", 200, url, trace
        )
    else:
        result = replace(
            _content("<body>BODY-MARKER</body>"),
            text="BODY-MARKER api_key=SECRET",
            title="BODY-MARKER",
            requisition_id="BODY-MARKER",
            resolved_url=url,
            trace=trace,
        )
    assert module._report(result, url) == int(failed)
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "page_chars: 73" in captured.out
    assert "frames: 5/4/2" in captured.out
    assert "selected_frame: 0" in captured.out
    assert "frame: 0 origin=about outcome=selected chars=1500" in captured.out
    for outcome in ("qualifying", "short", "unreadable", "over_limit"):
        assert f"outcome={outcome}" in captured.out
    assert "resolved_url: https://jobs.example.com/1" in captured.out
    assert "url_key: https://jobs.example.com/1" in captured.out
    for secret in ("BODY-MARKER", "SECRET", "password", "user:", "FRAGMENT"):
        assert secret not in captured.out


@pytest.mark.parametrize("trace", [None, AcquisitionTrace()])
def test_probe_handles_missing_steps(
    capsys: pytest.CaptureFixture[str], trace: AcquisitionTrace | None
) -> None:
    module = _load("probe_posting")
    result = AcquisitionFailure("transport", "private error", trace=trace)
    assert module._report(result, "not a URL") == 1
    output = capsys.readouterr().out
    assert "private error" not in output
    assert "url_key: <unparseable-url>" in output
    if trace is not None:
        assert "page_chars: 0" in output
        assert "frames: 0/0/0" in output
        assert "selected_frame: None" in output
