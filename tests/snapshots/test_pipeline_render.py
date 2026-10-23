from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from playwright.async_api import Browser, async_playwright

from job_discovery.settings import CaptureBrowser
from job_pipeline.content.render import UnsafeBrowserTargetError, render
from tests.unit.asyncio_harness import run_async


async def _public_dns(_host: str) -> set[str]:
    return {"93.184.216.34"}


class _OfflinePage:
    def __init__(self, page: Any, body: str) -> None:
        self.page = page
        self.body = body
        self.url = "https://jobs.example.com/posting/1"

    async def goto(self, _url: str, **_kwargs: Any) -> None:
        await self.page.set_content(self.body)

    async def evaluate(self, script: str) -> Any:
        return await self.page.evaluate(script)

    async def content(self) -> str:
        content = await self.page.content()
        if not isinstance(content, str):
            raise TypeError("browser content must be text")
        return content


class _OfflineContext:
    def __init__(self, context: Any, body: str) -> None:
        self.context = context
        self.body = body

    async def route(self, pattern: str, handler: Any) -> None:
        await self.context.route(pattern, handler)

    async def new_page(self) -> _OfflinePage:
        return _OfflinePage(await self.context.new_page(), self.body)

    async def close(self) -> None:
        await self.context.close()


class _OfflineBrowser:
    def __init__(self, browser: Browser, body: str) -> None:
        self.browser = browser
        self.body = body

    async def new_context(self, **kwargs: Any) -> _OfflineContext:
        context = await self.browser.new_context(**kwargs)
        return _OfflineContext(context, self.body)


@pytest.fixture(scope="session")
def browser_context_args(
    browser_context_args: dict[str, Any],
) -> dict[str, Any]:
    return {**browser_context_args, "java_script_enabled": True}


def test_render_captures_delayed_client_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_sleep = asyncio.sleep

    async def no_sleep(_seconds: float) -> None:
        await original_sleep(0.05)

    monkeypatch.setattr("job_pipeline.content.render.asyncio.sleep", no_sleep)

    async def scenario() -> str:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                body = (
                    "<script>setTimeout(() => "
                    "document.body.textContent = ['Rendered', ' posting'].join(''), "
                    "0)</script>"
                )
                offline = _OfflineBrowser(browser, body)
                capture = CaptureBrowser(cast(Browser, offline), "snapshot-test")
                assert "Rendered posting" not in body
                return await render(
                    "https://jobs.example.com/posting/1",
                    capture=capture,
                    resolver=_public_dns,
                )
            finally:
                await browser.close()

    assert "Rendered posting" in run_async(scenario())


def test_render_blocks_private_subrequests() -> None:
    async def resolver(host: str) -> set[str]:
        if host == "internal.example.com":
            return {"127.0.0.1"}
        return {"93.184.216.34"}

    async def scenario() -> str:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                body = (
                    "<script>setTimeout(() => fetch("
                    '"http://internal.example.com/secret"), 0)</script>'
                )
                offline = _OfflineBrowser(browser, body)
                capture = CaptureBrowser(cast(Browser, offline), "snapshot-test")
                return await render(
                    "https://jobs.example.com/posting/1",
                    capture=capture,
                    resolver=resolver,
                )
            finally:
                await browser.close()

    with pytest.raises(UnsafeBrowserTargetError) as error:
        run_async(scenario())
    assert error.value.category == "malformed"


def test_render_blocks_off_site_navigation() -> None:
    async def scenario() -> str:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                body = (
                    "<script>setTimeout(() => "
                    'location.href="https://evil.example/", 0)</script>'
                )
                offline = _OfflineBrowser(browser, body)
                capture = CaptureBrowser(cast(Browser, offline), "snapshot-test")
                return await render(
                    "https://jobs.example.com/posting/1",
                    capture=capture,
                    resolver=_public_dns,
                )
            finally:
                await browser.close()

    with pytest.raises(UnsafeBrowserTargetError) as error:
        run_async(scenario())
    assert error.value.category == "off_site_redirect"
