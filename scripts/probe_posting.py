"""Inspect deterministic posting acquisition for one live URL."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

import httpx
from playwright.async_api import async_playwright

from job_discovery.catalog import find_company
from job_discovery.settings import launch_capture_browser
from job_pipeline.content import AcquisitionFailure
from job_pipeline.content.ladder import acquire
from job_pipeline.content.model import AcquiredContent
from job_pipeline.runner.diagnostics import redact_url


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("-c", "--company", required=True)
    parser.add_argument("--no-browser", action="store_true")
    return parser


async def _run(args: argparse.Namespace) -> int:
    company = find_company(args.company)
    if company is None:
        raise SystemExit(f"unknown company: {args.company}")
    async with httpx.AsyncClient() as http:
        if args.no_browser:
            result = await acquire(args.url, company=company, http=http, browser=None)
        else:
            async with async_playwright() as playwright:
                capture = await launch_capture_browser(playwright)
                try:
                    result = await acquire(
                        args.url, company=company, http=http, browser=capture
                    )
                finally:
                    await capture.browser.close()
    return _report(result, args.url)


def _report(result: AcquiredContent | AcquisitionFailure, source_url: str) -> int:
    failed = isinstance(result, AcquisitionFailure)
    if isinstance(result, AcquisitionFailure):
        print(f"failure: {result.category}")
    else:
        print(f"method: {result.method}")
        print(f"text_length: {len(result.text)}")
    print(f"status: {result.http_status}")
    print(f"resolved_url: {redact_url(result.resolved_url or '')}")
    print(f"url_key: {redact_url(source_url)}")
    trace = result.trace
    if trace is not None:
        page_chars = trace.steps[-1].page_chars if trace.steps else 0
        inspected = sum(frame.outcome != "over_limit" for frame in trace.frames)
        qualifying = sum(
            frame.outcome in {"selected", "qualifying"} for frame in trace.frames
        )
        print(f"page_chars: {page_chars}")
        print(f"frames: {trace.frames_found}/{inspected}/{qualifying}")
        print(f"selected_frame: {trace.selected_frame}")
        for frame in trace.frames:
            print(
                f"frame: {frame.index} origin={frame.origin} "
                f"outcome={frame.outcome} chars={frame.chars}"
            )
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(_run(_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
