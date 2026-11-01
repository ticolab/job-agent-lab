from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import respx
from playwright.async_api import Browser, ElementHandle, Frame, async_playwright

from job_discovery.settings import CaptureBrowser
from job_pipeline.content.ladder import acquire
from job_pipeline.content.model import AcquiredContent, AcquisitionFailure
from job_pipeline.content.render import RenderedPage, render_page
from tests.snapshots.test_pipeline_render import _OfflineBrowser
from tests.unit.asyncio_harness import run_async
from tests.unit.test_pipeline_ladder import COMPANY, URL

FIXTURE = Path(__file__).parents[1] / "fixtures/framed_postings/recruitcrm.html"


@pytest.fixture(autouse=True)
def short_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    original_sleep = asyncio.sleep

    async def sleep(_seconds: float) -> None:
        await original_sleep(0.05)

    monkeypatch.setattr("job_pipeline.content.render.asyncio.sleep", sleep)


async def _dns(host: str) -> set[str]:
    return {"127.0.0.1"} if host == "internal.example.com" else {"93.184.216.34"}


def _body(variant: str) -> str:
    body = FIXTURE.read_text()
    if variant == "empty":
        return body[: body.index("<script>")]
    if variant == "closed":
        return body.replace(
            "Location: Mexicali, Mexico", "This position has been filled."
        )
    if variant == "hidden":
        return body.replace(
            'id="view_html_iframe"', 'style="display:none" id="view_html_iframe"'
        )
    if variant == "widget":
        return body.replace(
            "<script>",
            '<iframe width="300" height="200" '
            'srcdoc="<p>Unrelated consent widget</p>"></iframe><script>',
            1,
        )
    if variant == "private":
        return body.replace(
            "doc.close();",
            'doc.close(); doc.defaultView.fetch("https://internal.example.com/secret");',
        )
    if variant == "over_limit":
        return body.replace(
            "<script>",
            '<iframe width="200" height="150"></iframe>' * 24 + "<script>",
            1,
        )
    return body


@pytest.mark.parametrize(
    "variant",
    ["framed", "empty", "closed", "widget", "hidden", "private", "over_limit"],
)
def test_framed_acquisition_offline(variant: str) -> None:
    async def scenario() -> AcquiredContent | AcquisitionFailure:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                offline = _OfflineBrowser(browser, _body(variant))
                capture = CaptureBrowser(cast(Browser, offline), "snapshot-test")
                with respx.mock(assert_all_called=True) as mock:
                    mock.get(URL).mock(
                        return_value=httpx.Response(
                            200, text="<main>Short HTTP page</main>"
                        )
                    )
                    async with httpx.AsyncClient() as client:
                        return await acquire(
                            URL,
                            company=COMPANY,
                            http=client,
                            browser=capture,
                            resolver=_dns,
                        )
            finally:
                await browser.close()

    result = run_async(scenario())
    assert result.trace is not None
    if variant in {"empty", "hidden", "closed", "private"}:
        assert isinstance(result, AcquisitionFailure)
        assert (
            result.category
            == {
                "empty": "empty_content",
                "hidden": "empty_content",
                "closed": "closed",
                "private": "malformed",
            }[variant]
        )
    else:
        assert isinstance(result, AcquiredContent)
        assert result.method == "rendered_frame_main"
        assert result.title == "Platform Engineer"
        assert "Location: Mexicali, Mexico" in result.text
        assert "dependable scheduling services" in result.text
        assert "Unrelated consent widget" not in result.text
        assert result.frame_html and result.acquisition_html
        assert result.trace.selected_frame == 0
        assert result.trace.frames[0].outcome == "selected"
        assert result.trace.frames[0].element_title == "Job Description"
        if variant == "over_limit":
            assert result.trace.frames_found == 25
            assert [frame.outcome for frame in result.trace.frames[10:]] == [
                "over_limit"
            ] * 15


def test_structural_frame_rules_in_real_chromium(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.snapshots.test_pipeline_render import _OfflinePage

    async def goto(self: _OfflinePage, _url: str, **_kwargs: Any) -> None:
        await self.page.goto(self.url)
        await self.page.set_content(self.body)

    monkeypatch.setattr(_OfflinePage, "goto", goto)

    async def scenario() -> RenderedPage:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                body = """<html><body>
<iframe id="hidden" style="display:none"></iframe>
<iframe id="small" width="1" height="1"></iframe>
<iframe id="sandbox" sandbox width="200" height="150"></iframe>
<iframe id="allowed" sandbox="allow-same-origin" width="100" height="100"
 srcdoc="<p>Allowed sandbox</p>"></iframe>
<iframe id="sibling" width="200" height="150"
 src="https://media.jobs.example.com/frame"></iframe>
<iframe id="parent" width="200" height="150"
 srcdoc="<p>Parent only</p><iframe srcdoc='Nested frame'></iframe>"></iframe>
<iframe id="same" width="200" height="150"
 src="https://jobs.example.com/frame"></iframe>
<div id="shadow"></div>
<script>
const root = document.getElementById('shadow').attachShadow({mode:'open'});
root.innerHTML = '<iframe id="shadow-frame" width="200" height="150" ' +
 'srcdoc="<p>Shadow description</p>"></iframe>';
</script></body></html>"""
                capture = CaptureBrowser(
                    cast(Browser, _OfflineBrowser(browser, body)), "snapshot-test"
                )
                return await render_page(URL, capture=capture, resolver=_dns)
            finally:
                await browser.close()

    page = run_async(scenario())
    assert page.frames_found == 8
    assert [frame.outcome for frame in page.frames] == [
        "hidden",
        "too_small",
        "sandboxed",
        "candidate",
        "cross_origin",
        "candidate",
        "candidate",
        "candidate",
    ]
    assert page.frames[3].origin == "about"
    assert "Allowed sandbox" in page.frames[3].html
    assert page.frames[4].html == ""
    assert page.frames[6].origin == "same_origin"
    assert "Same origin" in page.frames[6].html
    assert page.frames[-1].element_id == "shadow-frame"
    assert "Shadow description" in page.frames[-1].html
    assert [frame.element_id for frame in page.frames].count("parent") == 1


def test_request_rejection_during_frame_capture_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_pipeline.content import render as render_module

    original = render_module._capture_frames

    async def capture_with_request(page: Any, page_url: str) -> Any:
        await page.evaluate("""() => {
            document.querySelector('iframe').contentWindow.fetch(
                'https://internal.example.com/during-capture');
        }""")
        await asyncio.Event().wait()
        return await original(page, page_url)

    monkeypatch.setattr(render_module, "_capture_frames", capture_with_request)

    async def scenario() -> AcquiredContent | AcquisitionFailure:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                capture = CaptureBrowser(
                    cast(Browser, _OfflineBrowser(browser, FIXTURE.read_text())),
                    "snapshot-test",
                )
                with respx.mock as mock:
                    mock.get(URL).mock(return_value=httpx.Response(200, text="short"))
                    async with httpx.AsyncClient() as http:
                        return await asyncio.wait_for(
                            acquire(
                                URL,
                                company=COMPANY,
                                http=http,
                                browser=capture,
                                resolver=_dns,
                            ),
                            timeout=15,
                        )
            finally:
                await browser.close()

    result = run_async(scenario())
    assert isinstance(result, AcquisitionFailure)
    assert result.category == "malformed"
    assert result.trace is not None and result.trace.steps[-1].step == "render"


@pytest.mark.parametrize(
    "mutation", ["before_read_navigation", "after_read_navigation", "sandbox"]
)
def test_frame_capture_discards_changed_documents(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    original_attribute = ElementHandle.get_attribute
    original_content = Frame.content
    changed = False

    async def mutate(frame: Frame) -> None:
        nonlocal changed
        changed = True
        if mutation == "sandbox":
            element = await frame.frame_element()
            await element.evaluate("element => element.setAttribute('sandbox', '')")
        else:
            await frame.goto("https://media.jobs.example.com/raced")

    async def attribute(self: ElementHandle, name: str, **kwargs: Any) -> str | None:
        value = await original_attribute(self, name, **kwargs)
        if name == "title" and mutation == "before_read_navigation" and not changed:
            frame = await self.content_frame()
            assert frame is not None
            await mutate(frame)
        return value

    async def content(self: Frame) -> str:
        value = await original_content(self)
        if (
            self.parent_frame is not None
            and mutation != "before_read_navigation"
            and not changed
        ):
            await mutate(self)
        return value

    monkeypatch.setattr(ElementHandle, "get_attribute", attribute)
    monkeypatch.setattr(Frame, "content", content)

    async def scenario() -> RenderedPage:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                capture = CaptureBrowser(
                    cast(Browser, _OfflineBrowser(browser, FIXTURE.read_text())),
                    "test",
                )
                return await render_page(URL, capture=capture, resolver=_dns)
            finally:
                await browser.close()

    result = run_async(scenario())
    assert changed
    assert result.frames_found == 1
    assert result.frames[0].outcome in {"cross_origin", "sandboxed", "unreadable"}
    assert result.frames[0].html == ""


@pytest.mark.parametrize("parent", ["main", "jsonld", "closed", "expired"])
def test_decisive_parent_skips_hanging_frame_reads(
    monkeypatch: pytest.MonkeyPatch,
    parent: str,
) -> None:
    from tests.unit.test_pipeline_ladder import LONG, _html

    payload = '{"@type":"JobPosting","description":"' + LONG + '"}'
    body = _html(body=LONG)
    if parent == "jsonld":
        body = _html(jsonld=payload)
    elif parent == "closed":
        body = _html(body=LONG + " This position has been filled.")
    elif parent == "expired":
        body = _html(
            body="short", jsonld='{"@type":"JobPosting","validThrough":"2020-01-01"}'
        )
    body += '<iframe width="200" height="150"></iframe>' * 10
    reads = 0
    original_content = Frame.content

    async def content(self: Frame) -> str:
        nonlocal reads
        if self.parent_frame is not None:
            reads += 1
            if parent == "expired":
                return _html(body=LONG)
            await asyncio.Event().wait()
        return await original_content(self)

    monkeypatch.setattr(Frame, "content", content)

    async def scenario() -> AcquiredContent | AcquisitionFailure:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                capture = CaptureBrowser(
                    cast(Browser, _OfflineBrowser(browser, body)), "test"
                )
                with respx.mock as mock:
                    mock.get(URL).mock(return_value=httpx.Response(200, text="short"))
                    async with httpx.AsyncClient() as http:
                        return await asyncio.wait_for(
                            acquire(
                                URL,
                                company=COMPANY,
                                http=http,
                                browser=capture,
                                resolver=_dns,
                            ),
                            timeout=15,
                        )
            finally:
                await browser.close()

    result = run_async(scenario())
    assert result.trace is not None and result.trace.frames_found == 10
    if parent == "expired":
        assert reads == 10
        assert isinstance(result, AcquiredContent)
        assert result.method == "rendered_frame_main"
    else:
        assert reads == 0
        assert result.trace.frames == () and result.trace.selected_frame is None
        if parent == "closed":
            assert (
                isinstance(result, AcquisitionFailure) and result.category == "closed"
            )
        else:
            assert isinstance(result, AcquiredContent)
            assert result.method == (
                "rendered_jsonld" if parent == "jsonld" else "rendered_main"
            )
