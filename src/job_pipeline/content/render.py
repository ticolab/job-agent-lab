"""One-shot rendered HTML fallback using an injected capture browser."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Request, Route

from job_discovery.settings import (
    RENDER_SCROLL_COUNT,
    RENDER_WAIT_SEC,
    CaptureBrowser,
    navigation_timeout_ms,
)
from job_pipeline.content.fetch import (
    AddressResolver,
    TargetResolutionError,
    redirect_host_allowed,
    validate_public_target,
)
from job_pipeline.content.model import FailureCategory


class UnsafeBrowserTargetError(Exception):
    def __init__(self, category: FailureCategory, message: str) -> None:
        super().__init__(message)
        self.category = category


async def render(
    url: str,
    *,
    capture: CaptureBrowser,
    resolver: AddressResolver | None = None,
) -> str:
    """Render one URL in a fresh isolated context and return serialized HTML."""
    original_host = await validate_public_target(url, resolver=resolver)
    context = await capture.browser.new_context(user_agent=capture.user_agent)
    blocked: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    def reject(category: FailureCategory, message: str) -> None:
        if not blocked.done():
            blocked.set_exception(UnsafeBrowserTargetError(category, message))

    async def intercept(route: Route, request: Request) -> None:
        try:
            host = await validate_public_target(request.url, resolver=resolver)
        except TargetResolutionError as exc:
            reject("transport", str(exc))
            await route.abort("blockedbyclient")
            return
        except ValueError as exc:
            reject("malformed", str(exc))
            await route.abort("blockedbyclient")
            return
        if request.is_navigation_request() and not redirect_host_allowed(
            original_host, host
        ):
            reject(
                "off_site_redirect",
                f"browser navigation left {original_host} for {host}",
            )
            await route.abort("blockedbyclient")
            return
        await route.fallback()

    async def checked(operation: Callable[[], Awaitable[object]]) -> object:
        task: asyncio.Future[Any] = asyncio.ensure_future(operation())
        done, _ = await asyncio.wait(
            {task, blocked}, return_when=asyncio.FIRST_COMPLETED
        )
        if blocked in done:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return blocked.result()
        try:
            return task.result()
        except PlaywrightError:
            if blocked.done():
                return blocked.result()
            raise

    try:
        await context.route("**/*", intercept)
        page = await context.new_page()
        await checked(
            lambda: page.goto(url, timeout=navigation_timeout_ms(RENDER_WAIT_SEC))
        )
        current_host = await validate_public_target(page.url, resolver=resolver)
        if not redirect_host_allowed(original_host, current_host):
            raise UnsafeBrowserTargetError(
                "off_site_redirect",
                f"browser navigation left {original_host} for {current_host}",
            )
        await checked(lambda: asyncio.sleep(RENDER_WAIT_SEC))
        for _ in range(RENDER_SCROLL_COUNT):
            await checked(
                lambda: page.evaluate(
                    "() => { window.scrollBy(0, window.innerHeight); }"
                )
            )
            await checked(lambda: asyncio.sleep(1))
        content = await checked(page.content)
        if not isinstance(content, str):
            raise TypeError("browser content must be text")
        return content
    finally:
        if not blocked.done():
            blocked.cancel()
        await context.close()
