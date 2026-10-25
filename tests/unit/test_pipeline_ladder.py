from __future__ import annotations

from typing import Any, cast

import httpx
import respx
from playwright.async_api import Browser

from job_discovery.domain.company import Company
from job_discovery.settings import CaptureBrowser
from job_pipeline.content.ladder import acquire
from job_pipeline.content.model import AcquiredContent, AcquisitionFailure
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
