from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx

from job_pipeline.content import fetch as fetch_module
from job_pipeline.content.fetch import FetchResult, fetch
from job_pipeline.content.model import AcquisitionFailure
from tests.unit.asyncio_harness import run_async

URL = "https://jobs.example.com/posting/1"


async def _public_dns(_host: str) -> set[str]:
    return {"93.184.216.34"}


def _run(response: httpx.Response | Callable[[httpx.Request], httpx.Response]) -> Any:
    async def scenario() -> Any:
        with respx.mock(assert_all_called=False) as mock:
            route = mock.get(URL)
            if callable(response):
                route.mock(side_effect=response)
            else:
                route.mock(return_value=response)
            async with httpx.AsyncClient() as client:
                return await fetch(URL, client=client, resolver=_public_dns)

    return run_async(scenario())


@pytest.mark.parametrize(
    ("status", "body", "category"),
    [
        (404, "", "not_found"),
        (410, "", "not_found"),
        (401, "", "blocked"),
        (403, "", "blocked"),
        (429, "", "blocked"),
        (503, "Cloudflare captcha challenge", "blocked"),
        (500, "oops", "server_error"),
        (503, "maintenance", "server_error"),
    ],
)
def test_status_mapping(status: int, body: str, category: str) -> None:
    result = _run(httpx.Response(status, text=body))
    assert isinstance(result, AcquisitionFailure)
    assert result.category == category
    assert result.http_status == status


def test_timeout_and_transport_mapping() -> None:
    for error, category in (
        (httpx.ReadTimeout("slow"), "timeout"),
        (httpx.ConnectError("offline"), "transport"),
    ):

        def raise_error(
            _request: httpx.Request, failure: httpx.TransportError = error
        ) -> httpx.Response:
            raise failure

        result = _run(raise_error)
        assert isinstance(result, AcquisitionFailure)
        assert result.category == category


def test_redirect_chain_records_final_url_and_user_agent() -> None:
    final = "https://boards.greenhouse.io/acme/jobs/7"

    async def scenario() -> Any:
        with respx.mock(assert_all_called=True) as mock:
            first = mock.get(URL).mock(
                return_value=httpx.Response(302, headers={"location": final})
            )
            mock.get(final).mock(return_value=httpx.Response(200, text="posting"))
            async with httpx.AsyncClient() as client:
                result = await fetch(URL, client=client, resolver=_public_dns)
            assert "Chrome/" in first.calls[0].request.headers["user-agent"]
            assert "HeadlessChrome" not in first.calls[0].request.headers["user-agent"]
            return result

    result = run_async(scenario())
    assert isinstance(result, FetchResult)
    assert result.final_url == final


def test_sibling_subdomain_redirect_is_allowed() -> None:
    final = "https://www.example.com/careers/posting/1"

    async def scenario() -> Any:
        with respx.mock(assert_all_called=True) as mock:
            mock.get(URL).mock(
                return_value=httpx.Response(301, headers={"location": final})
            )
            mock.get(final).mock(return_value=httpx.Response(200, text="posting"))
            async with httpx.AsyncClient() as client:
                return await fetch(URL, client=client, resolver=_public_dns)

    result = run_async(scenario())
    assert isinstance(result, FetchResult)
    assert result.final_url == final


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("www.acme.com", "acme.com"),
        ("acme.com", "acme.com"),
        ("careers.acme.co.cr", "acme.co.cr"),
        ("jobs.acme.com.mx", "acme.com.mx"),
        ("a.b.c.example.org", "example.org"),
        ("EXAMPLE.ORG.", "example.org"),
    ],
)
def test_registrable_domain(host: str, expected: str) -> None:
    assert fetch_module.registrable_domain(host) == expected


def test_off_site_redirect_is_rejected_before_it_is_requested() -> None:
    async def scenario() -> Any:
        with respx.mock(assert_all_called=True) as mock:
            mock.get(URL).mock(
                return_value=httpx.Response(
                    302, headers={"location": "https://evil.example/posting"}
                )
            )
            async with httpx.AsyncClient() as client:
                return await fetch(URL, client=client, resolver=_public_dns)

    result = run_async(scenario())
    assert isinstance(result, AcquisitionFailure)
    assert result.category == "off_site_redirect"


@pytest.mark.parametrize(
    "target",
    [
        "http://127.0.0.1/posting",
        "http://169.254.169.254/latest/meta-data",
        "http://192.0.2.1/posting",
        "http://[::1]/posting",
    ],
)
def test_non_public_redirect_is_rejected_before_it_is_requested(target: str) -> None:
    async def scenario() -> Any:
        with respx.mock(assert_all_called=True) as mock:
            mock.get(URL).mock(
                return_value=httpx.Response(302, headers={"location": target})
            )
            async with httpx.AsyncClient() as client:
                return await fetch(URL, client=client, resolver=_public_dns)

    result = run_async(scenario())
    assert isinstance(result, AcquisitionFailure)
    assert result.category == "malformed"
    assert "non-public" in result.message


def test_hostname_redirect_rejects_private_dns_resolution() -> None:
    calls = 0

    async def private_dns(_host: str) -> set[str]:
        nonlocal calls
        calls += 1
        return {"93.184.216.34"} if calls == 1 else {"10.0.0.8"}

    async def scenario() -> Any:
        with respx.mock(assert_all_called=True) as mock:
            mock.get(URL).mock(
                return_value=httpx.Response(
                    302, headers={"location": "https://jobs.example.com/internal"}
                )
            )
            async with httpx.AsyncClient() as client:
                return await fetch(URL, client=client, resolver=private_dns)

    result = run_async(scenario())
    assert isinstance(result, AcquisitionFailure)
    assert result.category == "malformed"
    assert "non-public" in result.message


def test_dns_failure_is_retryable_transport() -> None:
    async def failed_dns(_host: str) -> set[str]:
        raise fetch_module.TargetResolutionError("target could not be resolved")

    async def scenario() -> Any:
        async with httpx.AsyncClient() as client:
            return await fetch(URL, client=client, resolver=failed_dns)

    result = run_async(scenario())
    assert isinstance(result, AcquisitionFailure)
    assert result.category == "transport"


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@jobs.example.com/posting/1",
        "http://127.0.0.1/posting/1",
        "http://169.254.169.254/posting/1",
        "http://192.0.2.1/posting/1",
        "http://224.0.0.1/posting/1",
        "http://[::1]/posting/1",
    ],
)
def test_unsafe_initial_url_does_not_send_a_request(url: str) -> None:
    async def scenario() -> Any:
        with respx.mock(assert_all_called=True):
            async with httpx.AsyncClient() as client:
                return await fetch(url, client=client, resolver=_public_dns)

    result = run_async(scenario())
    assert isinstance(result, AcquisitionFailure)
    assert result.category == "malformed"


@pytest.mark.parametrize(
    ("board", "expected"),
    [
        ("https://jobs.example.com/jobs", "closed"),
        ("https://jobs.example.com/jobs?department=engineering", "success"),
        ("https://jobs.example.com/jobs?utm_source=email", "closed"),
    ],
)
def test_listing_root_compares_canonical_identity(board: str, expected: str) -> None:
    final = "https://jobs.example.com/jobs"

    async def scenario() -> Any:
        with respx.mock(assert_all_called=True) as mock:
            mock.get(URL).mock(
                return_value=httpx.Response(302, headers={"location": final})
            )
            mock.get(final).mock(return_value=httpx.Response(200, text="listings"))
            async with httpx.AsyncClient() as client:
                return await fetch(
                    URL,
                    client=client,
                    job_board_url=board,
                    resolver=_public_dns,
                )

    result = run_async(scenario())
    if expected == "closed":
        assert isinstance(result, AcquisitionFailure)
        assert result.category == "closed"
    else:
        assert isinstance(result, FetchResult)


def test_malformed_input_does_not_send_a_request() -> None:
    async def scenario() -> Any:
        async with httpx.AsyncClient() as client:
            return await fetch("file:///etc/passwd", client=client)

    result = run_async(scenario())
    assert isinstance(result, AcquisitionFailure)
    assert result.category == "malformed"
