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
from job_pipeline.identity import url_key


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
    if isinstance(result, AcquisitionFailure):
        print(f"failure: {result.category}")
        print(f"status: {result.http_status}")
        print(f"resolved_url: {result.resolved_url}")
        print(f"message: {result.message}")
        print(f"url_key: {url_key(args.url)}")
        return 1
    print(f"method: {result.method}")
    print(f"status: {result.http_status}")
    print(f"title: {result.title or ''}")
    print(f"requisition_id: {result.requisition_id or ''}")
    print(f"text_length: {len(result.text)}")
    print(f"url_key: {url_key(args.url)}")
    print("text:")
    print("\n".join(result.text.splitlines()[:40]))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(_run(_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
