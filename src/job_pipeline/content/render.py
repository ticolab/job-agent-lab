"""One-shot rendered HTML fallback using an injected capture browser."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, Literal
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, Request, Route

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
from job_pipeline.content.model import FailureCategory, FrameOrigin, FrameOutcome
from job_pipeline.settings import (
    FRAME_READ_TIMEOUT,
    MAX_CAPTURED_FRAMES,
    MIN_FRAME_HEIGHT_PX,
    MIN_FRAME_WIDTH_PX,
)


@dataclass(frozen=True, slots=True)
class FrameCapture:
    index: int
    url: str
    origin: FrameOrigin
    outcome: FrameOutcome | Literal["candidate"]
    element_id: str = ""
    element_title: str = ""
    html: str = ""


@dataclass(frozen=True, slots=True)
class RenderedPage:
    """Skipped capture preserves frames_found but leaves frames empty."""

    html: str
    url: str
    frames_found: int = 0
    frames: tuple[FrameCapture, ...] = ()


def _origin(url: str) -> tuple[str, str | None, int | None]:
    parsed = urlsplit(url)
    return (
        parsed.scheme.lower(),
        parsed.hostname,
        parsed.port
        if parsed.port is not None
        else (443 if parsed.scheme.lower() == "https" else 80),
    )


def _frame_origin(url: str, page_url: str) -> FrameOrigin:
    if url in {"about:blank", "about:srcdoc"}:
        return "about"
    return "same_origin" if _origin(url) == _origin(page_url) else "cross_origin"


async def _capture_frames(page: Page, page_url: str) -> tuple[FrameCapture, ...]:
    """Inspect top-level elements, piercing open shadow roots but not frame documents.

    Only the first bounded elements are inspected; each document read has its own
    timeout. Hidden, small, opaque sandbox and cross-origin documents are excluded.
    Reads are bound to a document handle; changed documents, URLs or sandbox
    attributes are discarded after serialization rather than relabeled as safe.
    """
    handles = await page.query_selector_all("iframe, frame")
    captures: list[FrameCapture] = []
    for index, handle in enumerate(handles):
        captured = FrameCapture(index, "", "cross_origin", "over_limit")
        if index >= MAX_CAPTURED_FRAMES:
            captures.append(captured)
            continue
        captured = replace(captured, outcome="unreadable")
        try:
            frame = await handle.content_frame()
            if frame is not None:
                captured = replace(
                    captured,
                    url=frame.url,
                    origin=_frame_origin(frame.url, page_url),
                    element_id=await handle.get_attribute("id") or "",
                    element_title=await handle.get_attribute("title") or "",
                )
                if not await handle.is_visible():
                    captured = replace(captured, outcome="hidden")
                else:
                    box = await handle.bounding_box()
                    sandbox = await handle.get_attribute("sandbox")
                    if (
                        box is None
                        or box["width"] < MIN_FRAME_WIDTH_PX
                        or box["height"] < MIN_FRAME_HEIGHT_PX
                    ):
                        captured = replace(captured, outcome="too_small")
                    elif (
                        sandbox is not None
                        and "allow-same-origin" not in sandbox.split()
                    ):
                        captured = replace(captured, outcome="sandboxed")
                    elif captured.origin == "cross_origin":
                        captured = replace(captured, outcome="cross_origin")
                    else:
                        document = await frame.evaluate_handle("document")
                        try:
                            html = await asyncio.wait_for(
                                frame.content(), FRAME_READ_TIMEOUT.total_seconds()
                            )
                            same_document = await frame.evaluate(
                                "original => original === document", document
                            )
                            final_sandbox = await handle.get_attribute("sandbox")
                            final_url = frame.url
                            final_origin = _frame_origin(final_url, page_url)
                            if final_origin == "cross_origin":
                                captured = replace(
                                    captured,
                                    url=final_url,
                                    origin=final_origin,
                                    outcome="cross_origin",
                                )
                            elif (
                                final_sandbox is not None
                                and "allow-same-origin" not in final_sandbox.split()
                            ):
                                captured = replace(captured, outcome="sandboxed")
                            elif (
                                final_url != captured.url
                                or final_sandbox != sandbox
                                or not same_document
                            ):
                                captured = replace(captured, outcome="unreadable")
                            else:
                                captured = replace(
                                    captured, outcome="candidate", html=html
                                )
                        finally:
                            await document.dispose()
        except (PlaywrightError, TimeoutError):
            captured = replace(captured, outcome="unreadable", html="")
        captures.append(captured)
    return tuple(captures)


class UnsafeBrowserTargetError(Exception):
    def __init__(self, category: FailureCategory, message: str) -> None:
        super().__init__(message)
        self.category = category


async def render_page(
    url: str,
    *,
    capture: CaptureBrowser,
    resolver: AddressResolver | None = None,
    capture_frames_if: Callable[[str, str], bool] | None = None,
) -> RenderedPage:
    """Render under the request guard; an optional HTML/URL predicate gates reads.

    A false predicate only counts top-level frame elements, including open shadow
    roots. No frame handles are inspected and the returned frames tuple is empty.
    By default all bounded candidates are read.
    """
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
        if capture_frames_if is not None and not capture_frames_if(content, page.url):
            handles = await checked(lambda: page.query_selector_all("iframe, frame"))
            if not isinstance(handles, list):
                raise TypeError("browser frame handles must be a list")
            return RenderedPage(content, page.url, len(handles))
        frames = await checked(lambda: _capture_frames(page, page.url))
        if not isinstance(frames, tuple):
            raise TypeError("browser frames must be a tuple")
        return RenderedPage(content, page.url, len(frames), frames)
    finally:
        if not blocked.done():
            blocked.cancel()
        await context.close()


async def render(
    url: str,
    *,
    capture: CaptureBrowser,
    resolver: AddressResolver | None = None,
) -> str:
    """Keep the serialized top-level HTML API for existing callers."""
    return (
        await render_page(
            url,
            capture=capture,
            resolver=resolver,
            capture_frames_if=lambda _html, _url: False,
        )
    ).html
