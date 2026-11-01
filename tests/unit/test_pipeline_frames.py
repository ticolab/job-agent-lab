from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, cast

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from job_pipeline.content.render import _capture_frames, _frame_origin
from tests.unit.asyncio_harness import run_async


class _Document:
    async def dispose(self) -> None:
        return None


class _Frame:
    url = "about:blank"

    def __init__(self, behavior: str) -> None:
        self.behavior = behavior

    async def evaluate_handle(self, _expression: str) -> _Document:
        return _Document()

    async def evaluate(self, _expression: str, _document: _Document) -> bool:
        return True

    async def content(self) -> str:
        if self.behavior == "error":
            raise PlaywrightError("detached")
        if self.behavior == "timeout":
            await asyncio.Event().wait()
        return "<p>Readable</p>"


class _Handle:
    def __init__(self, behavior: str) -> None:
        self.behavior = behavior
        self.reads = 0

    async def content_frame(self) -> _Frame | None:
        self.reads += 1
        if self.behavior == "detached":
            raise PlaywrightError("detached handle")
        return None if self.behavior == "missing" else _Frame(self.behavior)

    async def get_attribute(self, name: str) -> str | None:
        return "description" if name == "id" else None

    async def is_visible(self) -> bool:
        return True

    async def bounding_box(self) -> dict[str, float]:
        return {"width": 100, "height": 100}


class _Page:
    def __init__(self, handles: list[_Handle]) -> None:
        self.handles = handles

    async def query_selector_all(self, selector: str) -> list[_Handle]:
        assert selector == "iframe, frame"
        return self.handles


def test_frame_read_failures_are_isolated_and_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "job_pipeline.content.render.FRAME_READ_TIMEOUT", timedelta(milliseconds=10)
    )
    handles = [
        _Handle(behavior)
        for behavior in ("error", "timeout", "detached", "missing", "ok")
    ]
    frames = run_async(
        _capture_frames(cast(Page, _Page(handles)), "https://jobs.example.com/job")
    )
    assert [frame.outcome for frame in frames] == ["unreadable"] * 4 + ["candidate"]
    assert all(frame.html == "" for frame in frames[:4])
    assert frames[-1].html == "<p>Readable</p>"


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://jobs.example.com:443/frame", "same_origin"),
        ("https://jobs.example.com:0/frame", "cross_origin"),
        ("https://jobs.example.com:8443/frame", "cross_origin"),
        ("http://jobs.example.com/frame", "cross_origin"),
        ("about:blank", "about"),
        ("about:srcdoc", "about"),
        ("about:blank#fragment", "cross_origin"),
    ],
)
def test_frame_origin_preserves_explicit_ports(url: str, expected: str) -> None:
    assert _frame_origin(url, "https://jobs.example.com/job") == expected


def test_over_limit_handles_are_not_inspected() -> None:
    handles = [_Handle("ok") for _ in range(25)]
    frames = run_async(
        _capture_frames(cast(Any, _Page(handles)), "https://jobs.example.com/job")
    )
    assert len(frames) == 25
    assert [frame.outcome for frame in frames[10:]] == ["over_limit"] * 15
    assert [handle.reads for handle in handles] == [1] * 10 + [0] * 15
