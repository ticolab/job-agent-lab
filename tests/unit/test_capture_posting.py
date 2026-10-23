from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from job_pipeline.content.fetch import FetchResult
from job_pipeline.content.model import AcquiredContent, hash_content

ROOT = Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    path = ROOT / "scripts" / "capture_posting.py"
    spec = importlib.util.spec_from_file_location("capture_posting_test", path)
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
