from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from job_discovery.catalog import find_company
from job_discovery.domain.company import Company
from job_pipeline import settings
from job_pipeline.cli import common, stages
from job_pipeline.runner import acquisition, capture
from job_pipeline.runner.diagnostics import configure_logging

INTEGRATE_SUMMARY = "Capture one posting for manual acquisition review."
INTEGRATE_DESCRIPTION = INTEGRATE_SUMMARY
_LOG = logging.getLogger(__name__)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-c", "--company", required=True, metavar="HANDLE")
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--context",
        required=True,
        choices=("http-only", "browser-fallback", "forced-render"),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=common.positive_int,
        default=settings.DEFAULT_JOB_TIMEOUT,
        metavar="N",
    )
    common.add_verbosity_argument(parser)


def _attempt_metadata(attempt: acquisition.AcquisitionAttempt) -> dict[str, object]:
    result = attempt.result
    success = isinstance(result, acquisition.AcquiredContent)
    metadata: dict[str, object] = {
        "force_rendered": attempt.force_rendered,
        "elapsed_s": attempt.elapsed_s,
        "status": "success" if success else "failure",
        "category": result.category
        if isinstance(result, acquisition.AcquisitionFailure)
        else None,
        "http_status": result.http_status,
        "method": result.method
        if isinstance(result, acquisition.AcquiredContent)
        else None,
    }
    trace = result.trace
    if trace is not None:
        metadata["steps"] = [
            {
                "step": step.step,
                "http_status": step.http_status,
                "page_chars": step.page_chars,
                "has_jsonld": step.has_jsonld,
                "elapsed_s": step.elapsed_s,
            }
            for step in trace.steps
        ]
        metadata["frames_found"] = trace.frames_found
        metadata["selected_frame"] = trace.selected_frame
        metadata["frames"] = [
            {
                "index": frame.index,
                "origin": frame.origin,
                "outcome": frame.outcome,
                "chars": frame.chars,
            }
            for frame in trace.frames
        ]
    return metadata


async def _integrate(args: argparse.Namespace, company: Company) -> int:
    try:
        capture.preflight_output(args.output)
    except capture.CapturePublicationError as exc:
        print(f"publication_error: {exc.category}", file=sys.stderr)
        return 2 if exc.category == "destination_conflict" else 1
    try:
        await asyncio.wait_for(
            acquisition.validate_target(args.url), timeout=args.timeout_seconds
        )
    except ValueError:
        print("invalid_target: public HTTP(S) URL required", file=sys.stderr)
        return 2
    except Exception:
        print("startup_failed: target validation failed", file=sys.stderr)
        return 1

    attempts: list[dict[str, object]] = []

    def observe(attempt: acquisition.AcquisitionAttempt) -> None:
        metadata = _attempt_metadata(attempt)
        attempts.append(metadata)
        if args.verbose:
            _LOG.info(
                "Acquisition status=%s category=%s method=%s "
                "http_status=%s force_rendered=%s elapsed=%.2fs",
                metadata["status"],
                metadata["category"],
                metadata["method"],
                metadata["http_status"],
                attempt.force_rendered,
                attempt.elapsed_s,
            )
        if args.verbose > 1:
            _LOG.debug("Acquisition diagnostics: %s", metadata)

    started = False
    try:
        async with AsyncExitStack() as stack:
            http = await stack.enter_async_context(stages.http_client_factory())
            browser = None
            if args.context != "http-only":
                browser = await stack.enter_async_context(stages.browser_factory(True))
            resources = acquisition.AcquisitionResources(
                http_client=http,
                browser=browser,
                http_semaphore=asyncio.Semaphore(1),
                browser_semaphore=asyncio.Semaphore(1),
                per_host_semaphores={},
                per_host_lock=asyncio.Lock(),
                per_host_concurrency=1,
            )
            started = True
            result = await acquisition.acquire_posting(
                args.url,
                company=company,
                resources=resources,
                timeout_seconds=args.timeout_seconds,
                force_rendered=args.context == "forced-render",
                on_attempt=observe,
            )
            if isinstance(result, acquisition.AcquisitionFailure):
                print(f"acquisition_failed: {result.category}", file=sys.stderr)
    except Exception:
        label = "acquisition_failed" if started else "startup_failed"
        print(f"{label}: resource or acquisition error", file=sys.stderr)
        return 1

    try:
        capture.publish_capture(
            args.output,
            result=result,
            company_slug=company.slug,
            source_url=args.url,
            context=args.context,
            attempts=attempts,
        )
    except capture.CapturePublicationError as exc:
        label = (
            "secret_refusal"
            if exc.category == "secret_refusal"
            else "publication_error"
        )
        print(f"{label}: {exc.category}", file=sys.stderr)
        return 1
    except Exception:
        print("publication_error: capture publication failed", file=sys.stderr)
        return 1
    if isinstance(result, acquisition.AcquisitionFailure):
        print("acquisition_failed: diagnostic capture published", file=sys.stderr)
        return 1
    print("pending_review: capture published; manual review required")
    return 0


def execute(args: argparse.Namespace) -> None:
    configure_logging(args.verbose)
    company = find_company(args.company)
    if company is None:
        common.fail("invalid_company: unknown company handle")
    try:
        code = common.run_async(_integrate(args, company))
    except asyncio.CancelledError:
        raise KeyboardInterrupt from None
    except Exception:
        print("startup_failed: integration could not run", file=sys.stderr)
        raise SystemExit(1) from None
    if code:
        raise SystemExit(code)
