from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import respx
from playwright.async_api import Browser

from job_discovery.domain.company import Company
from job_discovery.settings import CaptureBrowser
from job_pipeline.content.fetch import FetchResult
from job_pipeline.content.ladder import _extract, _extract_rendered, acquire
from job_pipeline.content.model import AcquiredContent, AcquisitionFailure
from job_pipeline.content.render import FrameCapture, RenderedPage
from job_pipeline.settings import MIN_CONTENT_CHARS
from tests.unit.asyncio_harness import run_async

URL = "https://jobs.example.com/posting/1"
COMPANY = Company(
    name="Example",
    job_board_url="https://jobs.example.com/jobs",
    sample_job_url=URL,
)
LONG = " ".join(["Build reliable APIs and collaborate with engineers."] * 20)


async def _public_dns(_host: str) -> set[str]:
    return {"93.184.216.34"}


def _html(*, body: str = "", jsonld: str = "") -> str:
    script = f'<script type="application/ld+json">{jsonld}</script>' if jsonld else ""
    return (
        f"<html><head><title>Senior Engineer</title>{script}</head>"
        f"<body><main><h1>Senior Engineer</h1><p>{body}</p></main></body></html>"
    )


class _FakeContext:
    def __init__(self, html: str) -> None:
        self.html = html
        self.closed = False
        self.page = _FakePage(html)

    async def route(self, _pattern: str, _handler: Any) -> None:
        return None

    async def new_page(self) -> _FakePage:
        return self.page

    async def close(self) -> None:
        self.closed = True


class _FakePage:
    def __init__(self, html: str) -> None:
        self.html = html
        self.url = URL

    async def goto(self, _url: str, **_kwargs: Any) -> None:
        return None

    async def evaluate(self, _script: str) -> None:
        return None

    async def content(self) -> str:
        return self.html

    async def query_selector_all(self, _selector: str) -> list[Any]:
        return []


class _FakeBrowser:
    def __init__(self, html: str) -> None:
        self.html = html
        self.calls = 0
        self.contexts: list[_FakeContext] = []

    async def new_context(self, **_kwargs: Any) -> _FakeContext:
        self.calls += 1
        context = _FakeContext(self.html)
        self.contexts.append(context)
        return context


def _run(
    plain: str,
    *,
    rendered: str | None = None,
    monkeypatch: Any | None = None,
) -> tuple[AcquiredContent | AcquisitionFailure, _FakeBrowser | None]:
    async def scenario() -> tuple[
        AcquiredContent | AcquisitionFailure, _FakeBrowser | None
    ]:
        browser = _FakeBrowser(rendered) if rendered is not None else None
        capture = (
            CaptureBrowser(cast(Browser, browser), "UA")
            if browser is not None
            else None
        )
        with respx.mock(assert_all_called=True) as mock:
            mock.get(URL).mock(return_value=httpx.Response(200, text=plain))
            async with httpx.AsyncClient() as client:
                result = await acquire(
                    URL,
                    company=COMPANY,
                    http=client,
                    browser=capture,  # type: ignore[arg-type]
                    resolver=_public_dns,
                )
        return result, browser

    if monkeypatch is not None:

        async def no_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr("job_pipeline.content.render.asyncio.sleep", no_sleep)
    return run_async(scenario())


def test_jsonld_wins_and_appends_unique_main_paragraphs() -> None:
    description = LONG
    payload = (
        '{"@type":"JobPosting","title":"Structured title",'
        f'"identifier":{{"value":"REQ-1"}},"description":"{description}"}}'
    )
    result, browser = _run(
        _html(body=f"{description}</p><p>Extra details", jsonld=payload)
    )
    assert isinstance(result, AcquiredContent)
    assert result.method == "jsonld"
    assert result.title == "Structured title"
    assert result.requisition_id == "REQ-1"
    assert result.text.count(description) == 1
    assert "Extra details" in result.text
    assert result.acquisition_html == _html(
        body=f"{description}</p><p>Extra details", jsonld=payload
    )
    assert browser is None


def test_paragraph_dedup_is_case_sensitive() -> None:
    description = LONG
    payload = f'{{"@type":"JobPosting","description":"{description}"}}'
    result, _ = _run(_html(body=description.upper(), jsonld=payload))
    assert isinstance(result, AcquiredContent)
    assert description in result.text
    assert description.upper() in result.text


def test_paragraph_dedup_does_not_drop_containing_paragraphs() -> None:
    description = f"{LONG}"
    expanded = f"Context before {LONG} context after."
    payload = f'{{"@type":"JobPosting","description":"{description}"}}'
    result, _ = _run(_html(body=expanded, jsonld=payload))
    assert isinstance(result, AcquiredContent)
    assert description in result.text
    assert expanded in result.text


def test_main_content_wins_without_browser() -> None:
    result, browser = _run(_html(body=LONG))
    assert isinstance(result, AcquiredContent)
    assert result.method == "main_content"
    assert browser is None


def test_rendered_jsonld_is_used_after_short_plain_content(monkeypatch: Any) -> None:
    payload = f'{{"@type":"JobPosting","description":"{LONG}"}}'
    result, browser = _run(
        _html(body="short"), rendered=_html(jsonld=payload), monkeypatch=monkeypatch
    )
    assert isinstance(result, AcquiredContent)
    assert result.method == "rendered_jsonld"
    assert browser is not None and browser.calls == 1
    assert browser.contexts[0].closed


def test_rendered_main_is_used_after_short_plain_content(monkeypatch: Any) -> None:
    result, browser = _run(
        _html(body="short"), rendered=_html(body=LONG), monkeypatch=monkeypatch
    )
    assert isinstance(result, AcquiredContent)
    assert result.method == "rendered_main"
    assert browser is not None and browser.calls == 1


def test_closed_text_overrides_success() -> None:
    result, _ = _run(_html(body=f"{LONG} This position has been filled."))
    assert isinstance(result, AcquisitionFailure)
    assert result.category == "closed"


def test_empty_content_without_browser_is_classified() -> None:
    result, _ = _run(_html(body="short"))
    assert isinstance(result, AcquisitionFailure)
    assert result.category == "empty_content"
    assert str(MIN_CONTENT_CHARS) in result.message


EXPIRED = '"validThrough":"2020-01-01"'
NOW = datetime(2026, 11, 1, tzinfo=UTC)
FETCHED = FetchResult("", 200, URL, httpx.Headers())


def _frame(index: int, body: str, *, jsonld: str = "") -> FrameCapture:
    return FrameCapture(
        index,
        "about:blank",
        "about",
        "candidate",
        "description",
        "Job Description",
        _html(body=body, jsonld=jsonld),
    )


def _selected(
    page_html: str, *frames: FrameCapture
) -> AcquiredContent | AcquisitionFailure | None:
    return _extract_rendered(
        RenderedPage(page_html, URL, len(frames), frames),
        fetched=FETCHED,
        retrieved_at=NOW,
    )


def test_framed_composition_and_title() -> None:
    page = _html(body="Mexicali")
    frame = replace(
        _frame(0, f"{LONG}</p><p>Location: Mexicali, Mexico"),
        html=_html(body=f"{LONG}</p><p>Location: Mexicali, Mexico").replace(
            "Senior Engineer", "Job Description"
        ),
    )
    result = _selected(page, frame)
    assert isinstance(result, AcquiredContent)
    assert result.method == "rendered_frame_main"
    assert result.title == "Senior Engineer"
    assert "Mexicali" in result.text and LONG in result.text
    assert "Location: Mexicali, Mexico" in result.text
    assert result.acquisition_html == page and result.frame_html == frame.html
    assert result == _selected(page, frame)


def test_page_chrome_does_not_qualify_short_frame() -> None:
    assert _selected(_html(body="chrome " * 15), _frame(0, "x" * 350)) is None


def test_frame_ranking_longest_jsonld_and_document_order() -> None:
    widget = _frame(0, "Widget " * 80)
    description = _frame(1, LONG * 2)
    result = _selected(_html(body="short"), widget, description)
    assert isinstance(result, AcquiredContent)
    assert LONG in result.text and "Widget" not in result.text
    structured = _frame(
        2,
        "short",
        jsonld=json.dumps(
            {
                "@type": "JobPosting",
                "title": "Frame role",
                "identifier": {"value": "REQ-F"},
                "description": LONG,
            }
        ),
    )
    result = _selected(_html(), description, structured)
    assert isinstance(result, AcquiredContent)
    assert result.method == "rendered_frame_jsonld"
    assert result.title == "Frame role" and result.requisition_id == "REQ-F"
    first, second = _frame(0, "A" * 500), _frame(1, "B" * 500)
    result = _selected(_html(), second, first)
    assert isinstance(result, AcquiredContent)
    assert "A" * 500 in result.text and "B" * 500 not in result.text


@pytest.mark.parametrize(
    "body", ["This position has been filled.", LONG + " This position has been filled."]
)
def test_closed_frame(body: str) -> None:
    result = _selected(_html(), _frame(0, body))
    assert isinstance(result, AcquisitionFailure) and result.category == "closed"


def test_page_wins_over_frames_including_closed_frames() -> None:
    page = _html(body=LONG)
    expected = _extract(
        page, fetched=FETCHED, method_prefix="rendered", retrieved_at=NOW
    )
    assert _selected(page, _frame(0, "This position has been filled.")) == expected
    assert _selected(page, _frame(0, LONG * 2)) == expected
    closed = _selected(
        _html(body=LONG + " This position has been filled."), _frame(0, LONG)
    )
    assert isinstance(closed, AcquisitionFailure) and closed.category == "closed"


def test_frame_corroborates_stale_page_date_but_not_own_date() -> None:
    stale = json.dumps(
        {
            "@type": "JobPosting",
            "validThrough": "2020-01-01",
            "title": "Page role",
            "identifier": {"value": "PAGE"},
        }
    )
    result = _selected(_html(body="short", jsonld=stale), _frame(0, LONG * 2))
    assert isinstance(result, AcquiredContent)
    assert result.title == "Page role" and result.requisition_id == "PAGE"
    own = _selected(
        _html(),
        _frame(
            0,
            "short",
            jsonld=json.dumps(
                {
                    "@type": "JobPosting",
                    "validThrough": "2020-01-01",
                    "description": LONG,
                }
            ),
        ),
    )
    assert isinstance(own, AcquisitionFailure) and "validThrough" in own.message
    open_own = _selected(_html(), _frame(0, LONG, jsonld=stale))
    assert isinstance(open_own, AcquiredContent)


@pytest.mark.parametrize(
    "html",
    [
        _html(),
        _html(body=LONG),
        _html(body=LONG + " This position has been filled."),
        _html(jsonld=json.dumps({"@type": "JobPosting", "description": LONG})),
    ],
)
def test_nonframe_parity(html: str) -> None:
    assert _selected(html) == _extract(
        html, fetched=FETCHED, method_prefix="rendered", retrieved_at=NOW
    )


def test_nonframe_parity_existing_fixture_corpus() -> None:
    root = Path(__file__).parents[1] / "fixtures" / "snapshots"
    fixtures = sorted(root.glob("*/page.html"))
    assert fixtures
    for fixture in fixtures:
        html = fixture.read_text()
        assert _selected(html) == _extract(
            html, fetched=FETCHED, method_prefix="rendered", retrieved_at=NOW
        )


@pytest.mark.parametrize(
    "render_error", [None, TimeoutError("frame timeout"), ValueError("bad URL")]
)
def test_acquire_render_traces(
    monkeypatch: pytest.MonkeyPatch, render_error: Exception | None
) -> None:
    async def rendered(*_args: Any, **_kwargs: Any) -> RenderedPage:
        if render_error:
            raise render_error
        return RenderedPage(
            _html(body="short"),
            URL,
            2,
            (
                _frame(0, "short"),
                replace(_frame(1, "unused"), outcome="hidden", html=""),
            ),
        )

    monkeypatch.setattr("job_pipeline.content.ladder.render_page", rendered)
    result, _ = _run(_html(body="short"), rendered="unused")
    assert isinstance(result, AcquisitionFailure)
    assert result.trace is not None
    assert [step.step for step in result.trace.steps] == ["http", "render"]
    assert result.trace.steps[0].page_chars > 0
    assert all(step.elapsed_s >= 0 for step in result.trace.steps)
    if render_error is None:
        assert result.trace.frames_found == 2
        assert [frame.outcome for frame in result.trace.frames] == ["short", "hidden"]
        assert result.trace.frames[0].element_id == "description"


def test_qualifying_frame_wins_over_closed_sibling() -> None:
    result = _selected(
        _html(), _frame(0, "This position has been filled."), _frame(1, LONG)
    )
    assert isinstance(result, AcquiredContent)
    assert result.method == "rendered_frame_main"
    assert "filled" not in result.text


def test_forced_render_trace_and_jsonld_presence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = RenderedPage(
        _html(),
        URL,
        2,
        (
            _frame(0, LONG),
            _frame(
                1,
                "short",
                jsonld=json.dumps(
                    {
                        "@type": "JobPosting",
                        "description": LONG,
                        "title": "Frame role",
                    }
                ),
            ),
        ),
    )

    async def rendered(*_args: Any, **_kwargs: Any) -> RenderedPage:
        return page

    monkeypatch.setattr("job_pipeline.content.ladder.render_page", rendered)

    async def scenario() -> AcquiredContent | AcquisitionFailure:
        with respx.mock as mock:
            mock.get(URL).mock(return_value=httpx.Response(200, text=_html(body=LONG)))
            async with httpx.AsyncClient() as http:
                return await acquire(
                    URL,
                    company=COMPANY,
                    http=http,
                    browser=CaptureBrowser(cast(Browser, object()), "UA"),
                    force_rendered=True,
                    resolver=_public_dns,
                )

    result = run_async(scenario())
    assert isinstance(result, AcquiredContent)
    assert result.method == "rendered_frame_jsonld"
    assert result.trace is not None and result.trace.selected_frame == 1
    assert [frame.outcome for frame in result.trace.frames] == [
        "qualifying",
        "selected",
    ]
    assert result.trace.steps[0].page_chars >= MIN_CONTENT_CHARS
    assert not result.trace.steps[0].has_jsonld


def test_fetch_failure_trace() -> None:
    async def scenario() -> AcquisitionFailure | AcquiredContent:
        with respx.mock as mock:
            mock.get(URL).mock(return_value=httpx.Response(404))
            async with httpx.AsyncClient() as client:
                return await acquire(
                    URL,
                    company=COMPANY,
                    http=client,
                    browser=None,
                    resolver=_public_dns,
                )

    result = run_async(scenario())
    assert isinstance(result, AcquisitionFailure)
    assert result.trace is not None and len(result.trace.steps) == 1
    assert result.trace.steps[0].http_status == 404


def test_past_valid_through_alone_does_not_close_an_open_posting() -> None:
    payload = f'{{"@type":"JobPosting",{EXPIRED},"description":"{LONG}"}}'
    result, _ = _run(_html(body=LONG, jsonld=payload))
    assert isinstance(result, AcquiredContent)
    assert result.method == "jsonld"


def test_past_valid_through_with_short_body_is_closed_not_empty() -> None:
    payload = f'{{"@type":"JobPosting",{EXPIRED},"description":"Gone."}}'
    result, _ = _run(_html(body="short", jsonld=payload))
    assert isinstance(result, AcquisitionFailure)
    assert result.category == "closed"
    assert "validThrough" in result.message


def test_past_valid_through_with_closure_phrase_is_closed() -> None:
    payload = f'{{"@type":"JobPosting",{EXPIRED},"description":"{LONG}"}}'
    result, _ = _run(
        _html(body=f"{LONG} This position has been filled.", jsonld=payload)
    )
    assert isinstance(result, AcquisitionFailure)
    assert result.category == "closed"
