from __future__ import annotations

import argparse
import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx

from job_discovery.domain.company import Company
from job_discovery.settings import CaptureBrowser, launch_capture_browser
from job_pipeline.cli.common import (
    build_policy,
    open_database,
    require_provider_credential,
)
from job_pipeline.llm import LiteLLMCaller, StructuredCaller
from job_pipeline.persistence import jobs_repo
from job_pipeline.persistence.models import Stage
from job_pipeline.reporting import save_accepted_results
from job_pipeline.runner import PipelineResult, WorkerDeps, run_pipeline
from job_pipeline.runner.scheduler import browser_required


def caller_factory(model: str, timeout: float) -> StructuredCaller:
    return LiteLLMCaller(model, timeout=timeout)


_DEFAULT_CALLER_FACTORY = caller_factory


def preflight_provider(model: str) -> None:
    if caller_factory is _DEFAULT_CALLER_FACTORY and run_stage is _DEFAULT_RUN_STAGE:
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


def print_summary(result: PipelineResult, elapsed: float) -> None:
    print(f"Pipeline {result.stage} run: {result.run_id}")
    observation = result.observation
    if observation is not None:
        print(
            f"Observation: {observation.new} new, {observation.reseen} re-seen, "
            f"{observation.collapsed} collapsed, {observation.malformed} unkeyable"
        )
    for label, count in result.counts.items():
        if label not in {"prompt_tokens", "completion_tokens"}:
            print(f"{label:<22} {count}")
    print(
        f"{'tokens':<22} {result.tokens} "
        f"({result.prompt_tokens} prompt, {result.completion_tokens} completion)"
    )
    print(f"{'elapsed':<22} {elapsed:.2f}s")
    for outcome in result.outcomes:
        if outcome.error is not None:
            print(
                f"  {outcome.company_slug} {outcome.url_key} "
                f"{outcome.phase_reached} {outcome.error.category}: "
                f"{outcome.error.message}"
            )


async def run_stage(
    args: argparse.Namespace,
    companies: Sequence[Company],
    *,
    stage: Stage,
    reprocess: Sequence[int] | None = None,
    reason: str | None = None,
) -> PipelineResult:
    policy = build_policy(args)
    started = time.monotonic()
    async with open_database(args) as session_factory, AsyncExitStack() as resources:
        caller = caller_factory(policy.model, policy.job_timeout)
        http_client = None
        browser = None
        if stage == "eligibility":
            async with session_factory() as session:
                known_jobs = await jobs_repo.list_by_companies(
                    session, {company.slug for company in companies}
                )
            needs_browser = await browser_required(
                companies, known_jobs, session_factory
            )
            http_client = await resources.enter_async_context(http_client_factory())
            browser = await resources.enter_async_context(
                browser_factory(needs_browser)
            )

        def deps_factory(
            run_id: str,
            stage: Stage,
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
                stage=stage,
                companies={company.slug: company for company in companies},
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
            stage=stage,
            session_factory=session_factory,
            deps_factory=deps_factory,
            policy=policy,
            reprocess=reprocess,
            reason=reason,
        )
        if getattr(args, "json_output", None):
            written = await save_accepted_results(
                result, session_factory, Path(args.json_output)
            )
            print(f"JSON: {written} accepted result(s) written to {args.json_output}")
    print_summary(result, time.monotonic() - started)
    return result


_DEFAULT_RUN_STAGE = run_stage
