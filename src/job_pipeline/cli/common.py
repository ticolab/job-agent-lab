"""Shared selection, database preflight, and policy arguments."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import threading
from collections.abc import AsyncIterator, Coroutine, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, NoReturn, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import job_pipeline.settings as settings
from job_discovery.catalog import COMPANIES, find_company
from job_discovery.domain.company import Company
from job_discovery.persistence.engine import database, missing_tables
from job_pipeline.persistence import models
from job_pipeline.policy import PipelinePolicy


def fail(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    raise SystemExit(2)


def positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be 1 or greater, got {value}")
    return value


def provider_credential(model: str) -> tuple[str, str] | None:
    normalized = model.casefold()
    if normalized.startswith(("anthropic/", "claude-")):
        return "Anthropic", "ANTHROPIC_API_KEY"
    if normalized.startswith(("openai/", "gpt-", "chatgpt-")) or normalized in {
        "o1",
        "o3",
        "o4",
    }:
        return "OpenAI", "OPENAI_API_KEY"
    if normalized.startswith(("o1-", "o3-", "o4-")):
        return "OpenAI", "OPENAI_API_KEY"
    return None


def require_provider_credential(model: str) -> None:
    credential = provider_credential(model)
    if credential is None:
        return
    provider, variable = credential
    if not os.environ.get(variable):
        fail(f"{provider} requires {variable}.")


def run_async[T](coroutine: Coroutine[Any, Any, T]) -> T:
    result: dict[str, Any] = {}

    def invoke() -> None:
        try:
            result["value"] = asyncio.run(coroutine)
        except BaseException as exc:
            result["error"] = exc

    thread = threading.Thread(target=invoke)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return cast(T, result["value"])


def add_selection_arguments(parser: argparse.ArgumentParser) -> None:
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", action="store_true", default=False)
    selection.add_argument(
        "-c", "--company", action="append", metavar="HANDLE", default=None
    )
    selection.add_argument("--companies", metavar="FILE", default=None)
    parser.add_argument("--exclude", action="append", metavar="HANDLE", default=None)


def add_database_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--database", metavar="PATH", default=None)


def add_policy_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-m", "--model", default=settings.DEFAULT_MODEL)
    parser.add_argument("--limit", type=positive_int, default=None, metavar="N")
    parser.add_argument(
        "--http-concurrency",
        type=positive_int,
        default=settings.DEFAULT_HTTP_CONCURRENCY,
        metavar="N",
    )
    parser.add_argument(
        "--browser-concurrency",
        type=positive_int,
        default=settings.DEFAULT_BROWSER_CONCURRENCY,
        metavar="N",
    )
    parser.add_argument(
        "--model-concurrency",
        type=positive_int,
        default=settings.DEFAULT_MODEL_CONCURRENCY,
        metavar="N",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=positive_int,
        default=settings.DEFAULT_JOB_TIMEOUT,
        metavar="N",
    )


def build_policy(args: argparse.Namespace) -> PipelinePolicy:
    return PipelinePolicy(
        http_concurrency=args.http_concurrency,
        browser_concurrency=args.browser_concurrency,
        model_concurrency=args.model_concurrency,
        job_timeout=float(args.timeout_seconds),
        limit=args.limit,
        model=args.model,
    )


def _read_handles(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        fail(f"Cannot read --companies file '{path}': {exc}")
    return [
        handle
        for line in text.splitlines()
        if (handle := line.split("#", 1)[0].strip())
    ]


def _resolve(handles: Sequence[str]) -> tuple[list[Company], list[str]]:
    selected: list[Company] = []
    unknown: list[str] = []
    for handle in handles:
        company = find_company(handle)
        if company is None:
            unknown.append(handle)
        else:
            selected.append(company)
    return selected, unknown


def select_companies(args: argparse.Namespace) -> list[Company]:
    if args.all:
        selected = list(COMPANIES)
    else:
        handles = (
            _read_handles(Path(args.companies))
            if args.companies
            else list(args.company or ())
        )
        if not handles:
            fail("No company handles given. Pass --all, -c HANDLE, or --companies.")
        selected, unknown = _resolve(handles)
        if unknown:
            fail(f"Unknown company handle(s): {', '.join(unknown)}")
    if args.exclude:
        excluded, unknown = _resolve(args.exclude)
        if unknown:
            fail(f"Unknown --exclude handle(s): {', '.join(unknown)}")
        slugs = {company.slug for company in excluded}
        selected = [company for company in selected if company.slug not in slugs]
    result = list({company.slug: company for company in selected}.values())
    if not result:
        fail("No companies selected after exclusions.")
    return result


def _migrate_hint(path: Path | None) -> str:
    if path is None or path == settings.DATABASE_PATH:
        return "uv run alembic upgrade head"
    return f"{settings.DATABASE_ENV_VAR}={path} uv run alembic upgrade head"


@asynccontextmanager
async def open_database(
    args: argparse.Namespace,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    path = Path(args.database) if args.database else None
    assert models.Job.__tablename__ in models.Base.metadata.tables
    async with database(path) as session_factory:
        async with session_factory() as session:
            absent = await missing_tables(session)
        if absent:
            fail(
                f"Database schema is missing table(s): {', '.join(absent)}. "
                f"Run '{_migrate_hint(path)}' first."
            )
        yield session_factory


__all__ = [
    "add_database_argument",
    "add_policy_arguments",
    "add_selection_arguments",
    "build_policy",
    "fail",
    "open_database",
    "positive_int",
    "provider_credential",
    "require_provider_credential",
    "run_async",
    "select_companies",
]
