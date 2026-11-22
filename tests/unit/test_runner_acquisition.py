from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import cast

import httpx
import pytest

from job_discovery.settings import CaptureBrowser
from job_pipeline.content import fetch
from job_pipeline.runner import acquisition
from tests.unit.asyncio_harness import run_async
from tests.unit.test_pipeline_worker import COMPANY


def test_validate_target_uses_runtime_validator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def validate(url: str, *, resolver: fetch.AddressResolver | None) -> str:
        assert url == COMPANY.sample_job_url
        assert resolver is None
        return "validated.example"

    monkeypatch.setattr(fetch, "validate_public_target", validate)
    assert (
        run_async(acquisition.validate_target(COMPANY.sample_job_url))
        == "validated.example"
    )


@pytest.mark.parametrize(
    "url,address,error",
    [
        ("https://acme.example/jobs/1", "8.8.8.8", None),
        ("https://acme.example/jobs/1", "127.0.0.1", ValueError),
        ("https://acme.example/jobs/1", "invalid", fetch.TargetResolutionError),
        ("https://user:secret@acme.example/jobs/1", "8.8.8.8", ValueError),
        ("file:///etc/passwd", "8.8.8.8", ValueError),
    ],
)
def test_validate_target_public_dns(
    url: str, address: str, error: type[Exception] | None
) -> None:
    async def resolver(host: str) -> set[str]:
        assert host == "acme.example"
        return {address}

    async def scenario() -> None:
        if error is not None:
            with pytest.raises(error):
                await acquisition.validate_target(url, resolver=resolver)
        else:
            assert await acquisition.validate_target(url, resolver=resolver) == (
                "acme.example"
            )

    run_async(scenario())


def _resources(client: httpx.AsyncClient) -> acquisition.AcquisitionResources:
    return acquisition.AcquisitionResources(
        http_client=client,
        browser=cast(CaptureBrowser, object()),
        http_semaphore=asyncio.Semaphore(2),
        browser_semaphore=asyncio.Semaphore(1),
        per_host_semaphores={},
        per_host_lock=asyncio.Lock(),
        per_host_concurrency=1,
    )


def test_observer_reports_each_fallback_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = [
        acquisition.AcquisitionFailure("empty_content", "empty"),
        acquisition.AcquisitionFailure("blocked", "blocked"),
    ]

    async def acquire(
        *args: object, **kwargs: object
    ) -> acquisition.AcquisitionFailure:
        assert kwargs["company"] is COMPANY
        return results[int(bool(kwargs["force_rendered"]))]

    monkeypatch.setattr(acquisition, "acquire", acquire)
    clock = iter([10.0, 11.0, 20.0, 22.0])
    monkeypatch.setattr(acquisition, "monotonic", lambda: next(clock))

    async def scenario() -> None:
        attempts: list[acquisition.AcquisitionAttempt] = []
        async with httpx.AsyncClient() as client:
            resources = _resources(client)

            def observe(attempt: acquisition.AcquisitionAttempt) -> None:
                assert resources.per_host_semaphores["acme.example"].locked()
                assert resources.browser_semaphore.locked() is attempt.force_rendered
                attempts.append(attempt)

            result = await acquisition.acquire_posting(
                COMPANY.sample_job_url,
                company=COMPANY,
                resources=resources,
                timeout_seconds=7,
                on_attempt=observe,
            )
        assert result is results[1]
        assert attempts == [
            acquisition.AcquisitionAttempt(results[0], False, 1.0),
            acquisition.AcquisitionAttempt(results[1], True, 2.0),
        ]

    run_async(scenario())


@pytest.mark.parametrize("cancel", [False, True])
def test_timeout_and_cancellation_observation(
    monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        attempts: list[acquisition.AcquisitionAttempt] = []

        async def acquire(
            *args: object, **kwargs: object
        ) -> acquisition.AcquisitionFailure:
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        monkeypatch.setattr(acquisition, "acquire", acquire)
        async with httpx.AsyncClient() as client:
            resources = _resources(client)
            task = asyncio.create_task(
                acquisition.acquire_posting(
                    COMPANY.sample_job_url,
                    company=COMPANY,
                    resources=resources,
                    timeout_seconds=10 if cancel else 0.01,
                    force_rendered=True,
                    on_attempt=attempts.append,
                )
            )
            await asyncio.wait_for(entered.wait(), timeout=2)
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert attempts == []
            else:
                result = await task
                assert isinstance(result, acquisition.AcquisitionFailure)
                assert result.category == "timeout"
                assert result.resolved_url == COMPANY.sample_job_url
                assert len(attempts) == 1
                assert attempts[0].result is result
                assert attempts[0].force_rendered
                assert attempts[0].elapsed_s >= 0
            assert not resources.http_semaphore.locked()
            assert not resources.browser_semaphore.locked()
            assert not resources.per_host_semaphores["acme.example"].locked()

    run_async(scenario())


def test_resources_share_host_slots_across_callers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        calls: list[str] = []
        failure = acquisition.AcquisitionFailure("blocked", "blocked")

        async def acquire(url: str, **kwargs: object) -> acquisition.AcquisitionFailure:
            calls.append(url)
            entered.set()
            await release.wait()
            return failure

        monkeypatch.setattr(acquisition, "acquire", acquire)
        async with httpx.AsyncClient() as client:
            first = _resources(client)
            second = replace(first)
            assert second.per_host_semaphores is first.per_host_semaphores
            assert second.per_host_lock is first.per_host_lock
            task = asyncio.create_task(
                acquisition.acquire_posting(
                    COMPANY.sample_job_url,
                    company=COMPANY,
                    resources=first,
                    timeout_seconds=2,
                )
            )
            await asyncio.wait_for(entered.wait(), timeout=2)
            waiting = asyncio.create_task(
                acquisition.acquire_posting(
                    COMPANY.sample_job_url + "?second",
                    company=COMPANY,
                    resources=second,
                    timeout_seconds=2,
                )
            )
            try:
                await asyncio.sleep(0.02)
                assert calls == [COMPANY.sample_job_url]
            finally:
                release.set()
                results = await asyncio.gather(task, waiting)
            assert results == [failure, failure]
            assert len(calls) == 2
            assert len(first.per_host_semaphores) == 1

    run_async(scenario())
