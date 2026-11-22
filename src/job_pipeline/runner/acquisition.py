from __future__ import annotations

import asyncio
from asyncio import wait_for
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from urllib.parse import urlsplit

import httpx

from job_discovery.domain.company import Company
from job_discovery.settings import CaptureBrowser
from job_pipeline.content import fetch
from job_pipeline.content.fetch import AddressResolver
from job_pipeline.content.ladder import acquire
from job_pipeline.content.model import AcquiredContent, AcquisitionFailure


@dataclass(frozen=True, slots=True)
class AcquisitionResources:
    http_client: httpx.AsyncClient
    browser: CaptureBrowser | None
    http_semaphore: asyncio.Semaphore
    browser_semaphore: asyncio.Semaphore
    per_host_semaphores: dict[str, asyncio.Semaphore]
    per_host_lock: asyncio.Lock
    per_host_concurrency: int


@dataclass(frozen=True, slots=True)
class AcquisitionAttempt:
    result: AcquiredContent | AcquisitionFailure
    force_rendered: bool
    elapsed_s: float


async def validate_target(url: str, *, resolver: AddressResolver | None = None) -> str:
    return await fetch.validate_public_target(url, resolver=resolver)


async def acquire_posting(
    url: str,
    *,
    company: Company,
    resources: AcquisitionResources,
    timeout_seconds: float,
    force_rendered: bool = False,
    on_attempt: Callable[[AcquisitionAttempt], None] | None = None,
) -> AcquiredContent | AcquisitionFailure:
    async def attempt(
        *, browser: CaptureBrowser | None, force_rendered: bool
    ) -> AcquiredContent | AcquisitionFailure:
        started = monotonic()
        try:
            result = await wait_for(
                acquire(
                    url,
                    company=company,
                    http=resources.http_client,
                    browser=browser,
                    force_rendered=force_rendered,
                ),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            result = AcquisitionFailure(
                "timeout", "acquisition timed out", resolved_url=url
            )
        if on_attempt is not None:
            on_attempt(
                AcquisitionAttempt(result, force_rendered, monotonic() - started)
            )
        return result

    host = urlsplit(url).hostname or ""
    async with resources.per_host_lock:
        host_semaphore = resources.per_host_semaphores.get(host)
        if host_semaphore is None:
            host_semaphore = asyncio.Semaphore(resources.per_host_concurrency)
            resources.per_host_semaphores[host] = host_semaphore
    if force_rendered and resources.browser is not None:
        async with (
            resources.http_semaphore,
            host_semaphore,
            resources.browser_semaphore,
        ):
            return await attempt(browser=resources.browser, force_rendered=True)
    async with resources.http_semaphore, host_semaphore:
        acquired = await attempt(browser=None, force_rendered=False)
    if (
        isinstance(acquired, AcquisitionFailure)
        and acquired.category == "empty_content"
        and resources.browser is not None
    ):
        async with (
            resources.http_semaphore,
            host_semaphore,
            resources.browser_semaphore,
        ):
            return await attempt(browser=resources.browser, force_rendered=True)
    return acquired


__all__ = [
    "AcquiredContent",
    "AcquisitionAttempt",
    "AcquisitionFailure",
    "AcquisitionResources",
    "AddressResolver",
    "acquire_posting",
    "validate_target",
]
