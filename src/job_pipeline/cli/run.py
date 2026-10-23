"""The daily pipeline command and its injected composition factories."""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from dotenv import load_dotenv

from job_discovery.domain.company import Company
from job_discovery.settings import CaptureBrowser, launch_capture_browser
from job_pipeline.cli import preview as preview_command
from job_pipeline.cli.common import (
    add_database_argument,
    add_policy_arguments,
    add_selection_arguments,
    build_policy,
    open_database,
    require_provider_credential,
    run_async,
    select_companies,
)
from job_pipeline.llm import LiteLLMCaller, StructuredCaller
from job_pipeline.persistence import jobs_repo
from job_pipeline.reporting import save_accepted_results
from job_pipeline.runner import PipelineResult, WorkerDeps, run_pipeline
from job_pipeline.runner.scheduler import browser_required

RUN_SUMMARY = "Process selected job snapshots."
RUN_DESCRIPTION = "Observe selected companies and process every due posting."


def caller_factory(model: str, timeout: float) -> StructuredCaller:
    return LiteLLMCaller(model, timeout=timeout)


_DEFAULT_CALLER_FACTORY = caller_factory


def preflight_provider(model: str) -> None:
    if (
        caller_factory is _DEFAULT_CALLER_FACTORY
        and run_selected is _DEFAULT_RUN_SELECTED
    ):
        require_provider_credential(model)


@asynccontextmanager
async def http_client_factory() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


@asynccontextmanager
async def browser_factory(enabled: bool) -> AsyncIterator[CaptureBrowser | None]:
    if not enabled:
        yield None
        return
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        capture = await launch_capture_browser(playwright)
        try:
            yield capture
        finally:
            await capture.browser.close()


def add_arguments(parser: argparse.ArgumentParser) -> None:
    add_selection_arguments(parser)
    add_database_argument(parser)
    add_policy_arguments(parser)
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--json-output", metavar="DIR", default=None)


def print_summary(result: PipelineResult, elapsed: float) -> None:
    observation = result.observation
    print(f"Pipeline run: {result.run_id}")
    if observation is not None:
        print(
            "Observation: "
            f"{observation.new} new, {observation.reseen} re-seen, "
            f"{observation.collapsed} collapsed"
        )
    for label, count in (
        ("skipped", result.skipped),
        ("attempted", result.attempted),
        ("eligible", result.eligible),
        ("ineligible", result.ineligible),
        ("unknown", result.unknown),
        ("unavailable", result.unavailable),
        ("retry", result.retry),
        ("failed", result.failed),
        ("merged", result.merged),
        ("interrupted", result.interrupted),
    ):
        print(f"{label:<14} {count}")
    print(
        f"tokens         {result.tokens} "
        f"({result.prompt_tokens} prompt, {result.completion_tokens} completion)"
    )
    print(f"elapsed        {elapsed:.2f}s")
    failures = [outcome for outcome in result.outcomes if outcome.error is not None]
    if failures:
        print("Failures:")
        for outcome in failures:
            assert outcome.error is not None
            print(
                f"  {outcome.company_slug} {outcome.url_key} "
                f"{outcome.phase_reached} {outcome.error.category}: "
                f"{outcome.error.message}"
            )


async def run_selected(
    args: argparse.Namespace,
    companies: Sequence[Company],
    *,
    reprocess: Sequence[int] | None = None,
    reason: str | None = None,
) -> PipelineResult:
    policy = build_policy(args)
    started = time.monotonic()
    async with open_database(args) as session_factory:
        caller = caller_factory(policy.model, policy.job_timeout)
        async with session_factory() as session:
            known_jobs = await jobs_repo.list_by_companies(
                session, {company.slug for company in companies}
            )
        needs_browser = await browser_required(companies, known_jobs, session_factory)
        async with (
            http_client_factory() as http_client,
            browser_factory(needs_browser) as browser,
        ):
            company_map: Mapping[str, Company] = {
                company.slug: company for company in companies
            }

            def deps_factory(
                run_id: str,
                trigger: Literal["scheduled", "reprocess"],
                reason: str | None,
                http_semaphore: asyncio.Semaphore,
                browser_semaphore: asyncio.Semaphore,
                model_semaphore: asyncio.Semaphore,
                per_host_semaphores: dict[str, asyncio.Semaphore],
                per_host_lock: asyncio.Lock,
            ) -> WorkerDeps:
                return WorkerDeps(
                    session_factory=session_factory,
                    companies=company_map,
                    http_client=http_client,
                    browser=browser,
                    caller=caller,
                    policy=policy,
                    run_id=run_id,
                    trigger=trigger,
                    reason=reason,
                    http_semaphore=http_semaphore,
                    browser_semaphore=browser_semaphore,
                    model_semaphore=model_semaphore,
                    per_host_semaphores=per_host_semaphores,
                    per_host_lock=per_host_lock,
                )

            result = await run_pipeline(
                companies,
                session_factory=session_factory,
                deps_factory=deps_factory,
                policy=policy,
                reprocess=reprocess,
                reason=reason,
            )
        if args.json_output:
            written = await save_accepted_results(
                result, session_factory, Path(args.json_output)
            )
            print(f"JSON: {written} accepted result(s) written to {args.json_output}")
    print_summary(result, time.monotonic() - started)
    return result


_DEFAULT_RUN_SELECTED = run_selected


def execute(args: argparse.Namespace) -> None:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    companies = select_companies(args)
    if args.dry_run:
        args.no_observe = False
        args.by_company = False
        run_async(preview_command.preview(args, companies))
        return
    preflight_provider(args.model)
    run_async(run_selected(args, companies))


__all__ = [
    "RUN_DESCRIPTION",
    "RUN_SUMMARY",
    "add_arguments",
    "browser_factory",
    "caller_factory",
    "execute",
    "http_client_factory",
    "preflight_provider",
    "print_summary",
    "run_selected",
]
