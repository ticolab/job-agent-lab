"""Bounded HTTP acquisition with posting-specific failure classification."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from job_discovery.settings import derive_plausible_ua
from job_pipeline.content.model import AcquisitionFailure
from job_pipeline.identity import url_key
from job_pipeline.settings import FETCH_TIMEOUT

MODERN_CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/141.0.0.0 Safari/537.36"
)
KNOWN_ATS_HOSTS = frozenset(
    {
        "greenhouse.io",
        "myworkdayjobs.com",
        "icims.com",
        "lever.co",
        "ashbyhq.com",
        "smartrecruiters.com",
        "oraclecloud.com",
        "successfactors.com",
        "csod.com",
        "ultipro.com",
        "bamboohr.com",
        "phenompeople.com",
    }
)
_CHALLENGE_MARKERS = (
    "captcha",
    "cloudflare",
    "challenge-platform",
    "access denied",
    "verify you are human",
    "checking your browser",
)


@dataclass(frozen=True, slots=True)
class FetchResult:
    html: str
    status: int
    final_url: str
    headers: httpx.Headers


class TargetResolutionError(Exception):
    pass


AddressResolver = Callable[[str], Awaitable[Collection[str]]]


def _validated_url(url: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or parsed.hostname is None:
        raise ValueError("URL must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL credentials are not allowed")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("URL has an invalid port") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("URL has an invalid port")
    return parsed.scheme.lower(), parsed.hostname.rstrip(".").lower()


def _host_matches(host: str, candidate: str) -> bool:
    return host == candidate or host.endswith(f".{candidate}")


def _unsafe_address(address: str) -> bool:
    value = ipaddress.ip_address(address)
    return (
        not value.is_global
        or value.is_private
        or value.is_loopback
        or value.is_link_local
        or value.is_reserved
        or value.is_multicast
    )


async def resolve_host_addresses(host: str) -> Collection[str]:
    loop = asyncio.get_running_loop()
    try:
        records = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise TargetResolutionError("target could not be resolved") from exc
    addresses = {record[4][0] for record in records}
    if not addresses:
        raise TargetResolutionError("target could not be resolved")
    return addresses


async def validate_public_target(
    url: str, *, resolver: AddressResolver | None = None
) -> str:
    _, host = _validated_url(url)
    resolve = resolver or resolve_host_addresses
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        addresses = await resolve(host)
    else:
        addresses = {str(address)}
    try:
        unsafe = any(_unsafe_address(address) for address in addresses)
    except ValueError as exc:
        raise TargetResolutionError("target resolved to an invalid address") from exc
    if unsafe:
        raise ValueError("target resolves to a non-public address")
    return host


def redirect_host_allowed(original_host: str, final_host: str) -> bool:
    return original_host == final_host or any(
        _host_matches(final_host, ats_host) for ats_host in KNOWN_ATS_HOSTS
    )


def _is_listing_root(final_url: str, job_board_url: str | None) -> bool:
    if job_board_url is None:
        return False
    try:
        final = urlsplit(url_key(final_url))
        board = urlsplit(url_key(job_board_url))
    except ValueError:
        return False
    return final == board


def _status_failure(response: httpx.Response) -> AcquisitionFailure | None:
    status = response.status_code
    final_url = str(response.url)
    if status in {404, 410}:
        return AcquisitionFailure("not_found", f"HTTP {status}", status, final_url)
    body = response.text.casefold()
    if status in {401, 403, 429} or (
        status == 503 and any(marker in body for marker in _CHALLENGE_MARKERS)
    ):
        return AcquisitionFailure("blocked", f"HTTP {status}", status, final_url)
    if status >= 500:
        return AcquisitionFailure("server_error", f"HTTP {status}", status, final_url)
    return None


async def fetch(
    url: str,
    *,
    client: httpx.AsyncClient,
    job_board_url: str | None = None,
    resolver: AddressResolver | None = None,
) -> FetchResult | AcquisitionFailure:
    """Fetch one posting with bounded redirects and classified failures."""
    try:
        original_host = await validate_public_target(url, resolver=resolver)
    except TargetResolutionError as exc:
        return AcquisitionFailure("transport", str(exc), resolved_url=url)
    except ValueError as exc:
        return AcquisitionFailure("malformed", str(exc), resolved_url=url)

    request = client.build_request(
        "GET",
        url,
        headers={"User-Agent": derive_plausible_ua(MODERN_CHROME_UA)},
        timeout=FETCH_TIMEOUT.total_seconds(),
    )
    redirects = 0
    response: httpx.Response | None = None
    redirected = False
    try:
        while True:
            response = await client.send(request, follow_redirects=False)
            if not response.has_redirect_location:
                break
            redirected = True
            redirects += 1
            if redirects > 10:
                return AcquisitionFailure(
                    "transport",
                    "redirect limit exceeded",
                    response.status_code,
                    str(response.url),
                )
            await response.aread()
            next_request = response.next_request
            if next_request is None:
                break
            request = next_request
            try:
                next_host = await validate_public_target(
                    str(request.url), resolver=resolver
                )
            except TargetResolutionError as exc:
                return AcquisitionFailure(
                    "transport", str(exc), response.status_code, str(request.url)
                )
            except ValueError as exc:
                return AcquisitionFailure(
                    "malformed", str(exc), response.status_code, str(request.url)
                )
            if not redirect_host_allowed(original_host, next_host):
                return AcquisitionFailure(
                    "off_site_redirect",
                    f"redirected from {original_host} to {next_host}",
                    response.status_code,
                    str(request.url),
                )
    except httpx.TimeoutException as exc:
        return AcquisitionFailure("timeout", str(exc), resolved_url=str(request.url))
    except httpx.TransportError as exc:
        return AcquisitionFailure("transport", str(exc), resolved_url=str(request.url))

    if response is None:
        return AcquisitionFailure("transport", "no response received", resolved_url=url)
    final_url = str(response.url)
    try:
        _, final_host = _validated_url(final_url)
    except ValueError as exc:
        return AcquisitionFailure(
            "malformed", str(exc), response.status_code, final_url
        )
    if redirected:
        if not redirect_host_allowed(original_host, final_host):
            return AcquisitionFailure(
                "off_site_redirect",
                f"redirected from {original_host} to {final_host}",
                response.status_code,
                final_url,
            )
        if _is_listing_root(final_url, job_board_url):
            return AcquisitionFailure(
                "closed",
                "posting redirected to the company listing root",
                response.status_code,
                final_url,
            )

    failure = _status_failure(response)
    if failure is not None:
        return failure
    return FetchResult(response.text, response.status_code, final_url, response.headers)
