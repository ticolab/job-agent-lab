"""Render complete pipeline audit metadata without posting bodies."""

from __future__ import annotations

import argparse
import json
from dataclasses import fields
from typing import Any

from job_pipeline.cli.common import (
    add_database_argument,
    fail,
    open_database,
    run_async,
)
from job_pipeline.persistence import (
    attempts_repo,
    contents_repo,
    decisions_repo,
    jobs_repo,
    results_repo,
)

SHOW_SUMMARY = "Show the complete audit history for one job."
SHOW_DESCRIPTION = "Print job metadata, sources, versions, attempts, and results."


def add_arguments(parser: argparse.ArgumentParser) -> None:
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--job", type=int, metavar="ID")
    selection.add_argument("--url", metavar="URL")
    add_database_argument(parser)


def _value(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _print_record(record: Any, *, omit: frozenset[str] = frozenset()) -> None:
    for field in fields(record):
        if field.name not in omit:
            print(f"  {field.name}: {_value(getattr(record, field.name))}")


async def _run(args: argparse.Namespace) -> None:
    async with open_database(args) as session_factory:
        if args.job is not None:
            if args.job < 1:
                fail("--job ID must be 1 or greater.")
            async with session_factory() as session:
                job = await jobs_repo.get(session, args.job)
        else:
            async with session_factory() as session:
                matches = await jobs_repo.list_by_url(session, args.url)
            if not matches:
                job = None
            elif len(matches) > 1:
                fail(
                    "URL matches multiple jobs; use --job with one of: "
                    + ", ".join(str(match.id) for match in matches)
                )
            else:
                job = matches[0]
        if job is None:
            fail("Job not found.")
        async with session_factory() as session:
            sources = await jobs_repo.list_sources(session, job.id)
            contents = await contents_repo.history(session, job.id)
            attempts = await attempts_repo.history(session, job.id)
            decisions = await decisions_repo.history(session, job.id)
            current_result = await results_repo.current(session, job.id)

    print("Job")
    _print_record(job)
    print("Sources")
    for source in sources:
        _print_record(source)
    print("Content versions")
    for content in contents:
        _print_record(content, omit=frozenset({"text", "jsonld_json"}))
        print(f"  text_chars: {len(content.text)}")
        print(f"  has_jsonld: {content.jsonld_json is not None}")
    print("Attempts")
    for attempt in attempts:
        _print_record(attempt)
    print("Decisions")
    for decision in decisions:
        _print_record(decision, omit=frozenset({"evidence_json"}))
        try:
            evidence = json.loads(decision.evidence_json)
        except json.JSONDecodeError:
            evidence = decision.evidence_json
        print("  evidence:")
        print(json.dumps(evidence, indent=4, ensure_ascii=False))
    print("Current result")
    if current_result is None:
        print("  (none)")
    else:
        _print_record(current_result, omit=frozenset({"result_json"}))
        try:
            payload = json.loads(current_result.result_json)
        except json.JSONDecodeError:
            payload = current_result.result_json
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))


def execute(args: argparse.Namespace) -> None:
    run_async(_run(args))


__all__ = ["SHOW_DESCRIPTION", "SHOW_SUMMARY", "add_arguments", "execute"]
