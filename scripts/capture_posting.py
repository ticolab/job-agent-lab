"""Capture one posting fixture without selecting or fabricating a corpus."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from playwright.async_api import async_playwright

from job_discovery.catalog import find_company
from job_discovery.settings import launch_capture_browser
from job_pipeline.content import AcquisitionFailure
from job_pipeline.content.fetch import FetchResult
from job_pipeline.content.ladder import acquire
from job_pipeline.identity import url_key

_SECRET_PATTERNS = (
    re.compile(
        r"(?i)(?:access[_-]?token|api[_-]?key|client[_-]?secret|password)\s*[=:]\s*[^\s\"'&<>]+"
    ),
    re.compile(
        r"(?i)(?:[?&](?:token|access_token|api_key|key|secret)=)(?!REDACTED(?:[&#]|$))[^&#\s\"'<>]+"
    ),
    re.compile(
        r"(?i)(?:authorization\s*[=:]\s*(?:bearer|basic)\s+)[A-Za-z0-9._~+/-]+=*"
    ),
    re.compile(r"(?i)(?:set-cookie|cookie)\s*:"),
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("-c", "--company", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rendered", action="store_true")
    return parser


def _sanitized_url(url: str) -> str:
    parsed = urlsplit(url)
    query = urlencode(
        [
            (name, "REDACTED" if "token" in name.casefold() else value)
            for name, value in parse_qsl(parsed.query, keep_blank_values=True)
        ]
    )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))


def _secret_findings(*values: str) -> list[str]:
    findings: list[str] = []
    for value in values:
        for pattern in _SECRET_PATTERNS:
            if pattern.search(value):
                findings.append(pattern.pattern)
    return findings


def _atomic_fixture(
    output: Path, raw: FetchResult, result: object, source_url: str
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(output)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        _write_fixture(temporary, raw, result, source_url)
        os.replace(temporary, output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


async def _run(args: argparse.Namespace) -> int:
    company = find_company(args.company)
    if company is None:
        raise SystemExit(f"unknown company: {args.company}")
    async with httpx.AsyncClient() as http:
        capture = None
        async with async_playwright() as playwright:
            launched = await launch_capture_browser(playwright)
            try:
                capture = launched
                result = await acquire(
                    args.url,
                    company=company,
                    http=http,
                    browser=capture,
                    force_rendered=args.rendered,
                )
            finally:
                await launched.browser.close()
    if isinstance(result, AcquisitionFailure):
        print(f"capture failed: {result.category}: {result.message}")
        return 1

    raw = FetchResult(
        result.acquisition_html,
        result.http_status,
        result.resolved_url,
        httpx.Headers(),
    )
    try:
        _atomic_fixture(args.output, raw, result, args.url)
    except (FileExistsError, OSError, ValueError) as exc:
        print(f"capture failed: {exc}")
        return 1
    return 0


def _write_fixture(
    output: Path, raw: FetchResult, result: object, source_url: str
) -> None:
    from job_pipeline.content.model import AcquiredContent

    if not isinstance(result, AcquiredContent):
        raise TypeError("successful acquisition required")
    captured_at = datetime.now(UTC).isoformat()
    metadata = {
        "url": _sanitized_url(source_url),
        "url_key": url_key(_sanitized_url(source_url)),
        "resolved_url": _sanitized_url(result.resolved_url),
        "method": result.method,
        "http_status": result.http_status,
        "requisition_id": result.requisition_id,
        "captured_at": captured_at,
        "content_hash": result.content_hash,
    }
    metadata_text = f"{json.dumps(metadata, indent=2, sort_keys=True)}\n"
    findings = _secret_findings(raw.html, result.text, metadata_text)
    if findings:
        raise ValueError("possible secret detected; no fixture was written")
    (output / "page.html").write_text(raw.html, encoding="utf-8")
    (output / "content.txt").write_text(f"{result.text}\n", encoding="utf-8")
    (output / "meta.json").write_text(metadata_text, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(_run(_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
