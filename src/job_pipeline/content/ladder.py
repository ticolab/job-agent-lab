"""Ordered deterministic posting-content acquisition ladder."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from job_discovery.domain.company import Company
from job_discovery.settings import CaptureBrowser
from job_pipeline.content.fetch import (
    AddressResolver,
    FetchResult,
    TargetResolutionError,
    fetch,
)
from job_pipeline.content.jsonld import JobPostingLd, extract_job_postings
from job_pipeline.content.model import (
    AcquiredContent,
    AcquisitionFailure,
    AcquisitionMethod,
    hash_content,
)
from job_pipeline.content.render import UnsafeBrowserTargetError, render
from job_pipeline.content.text import looks_closed, main_text, normalize
from job_pipeline.settings import MIN_CONTENT_CHARS


def _deduplicate_paragraphs(*texts: str) -> str:
    seen: set[str] = set()
    paragraphs: list[str] = []
    for text in texts:
        for paragraph in normalize(text).split("\n"):
            key = " ".join(paragraph.split())
            if key and key not in seen:
                seen.add(key)
                paragraphs.append(paragraph)
    return normalize("\n".join(paragraphs))


def _best_posting(postings: tuple[JobPostingLd, ...]) -> JobPostingLd | None:
    return max(
        postings, key=lambda posting: len(posting.description_text), default=None
    )


def _closed_failure(
    message: str, fetched: FetchResult, *, resolved_url: str | None = None
) -> AcquisitionFailure:
    return AcquisitionFailure(
        "closed", message, fetched.status, resolved_url or fetched.final_url
    )


def _extract(
    html: str,
    *,
    fetched: FetchResult,
    method_prefix: str,
    retrieved_at: datetime,
) -> AcquiredContent | AcquisitionFailure | None:
    postings = extract_job_postings(html)
    posting = _best_posting(postings)
    main, page_title = main_text(html, fetched.final_url)
    # A past validThrough alone is not proof: some ATSs leave a stale or
    # placeholder date on a posting that discovery still lists. It counts
    # only when the page corroborates it, with a closure phrase or a body
    # too short to be an open posting.
    if (
        posting is not None
        and posting.is_closed()
        and (looks_closed(main) or len(main) < MIN_CONTENT_CHARS)
    ):
        return _closed_failure("JSON-LD validThrough is in the past", fetched)

    if posting is not None and len(posting.description_text) >= MIN_CONTENT_CHARS:
        text = _deduplicate_paragraphs(posting.description_text, main)
        method: AcquisitionMethod = "rendered_jsonld" if method_prefix else "jsonld"
        title = posting.title or page_title
        selected_jsonld: dict[str, Any] | None = posting.raw
        requisition_id = posting.identifier
    elif len(main) >= MIN_CONTENT_CHARS:
        text = main
        method = "rendered_main" if method_prefix else "main_content"
        title = page_title or (posting.title if posting is not None else None)
        selected_jsonld = posting.raw if posting is not None else None
        requisition_id = posting.identifier if posting is not None else None
    else:
        return None

    if looks_closed(text):
        return _closed_failure("posting text indicates the job is closed", fetched)
    return AcquiredContent(
        text=text,
        title=title,
        resolved_url=fetched.final_url,
        http_status=fetched.status,
        method=method,
        requisition_id=requisition_id,
        jsonld=selected_jsonld,
        retrieved_at=retrieved_at,
        content_hash=hash_content(text),
        acquisition_html=html,
    )


async def acquire(
    url: str,
    *,
    company: Company,
    http: httpx.AsyncClient,
    browser: CaptureBrowser | None,
    force_rendered: bool = False,
    resolver: AddressResolver | None = None,
) -> AcquiredContent | AcquisitionFailure:
    """Run HTTP, structured data, main text, and optional render in order."""
    fetched = await fetch(
        url,
        client=http,
        job_board_url=company.job_board_url,
        resolver=resolver,
    )
    if isinstance(fetched, AcquisitionFailure):
        return fetched

    retrieved_at = datetime.now(UTC)
    if not force_rendered:
        plain = _extract(
            fetched.html, fetched=fetched, method_prefix="", retrieved_at=retrieved_at
        )
        if plain is not None:
            return plain
    if browser is None:
        return AcquisitionFailure(
            "empty_content",
            f"content is shorter than {MIN_CONTENT_CHARS} characters",
            fetched.status,
            fetched.final_url,
        )

    try:
        rendered_html = await render(
            fetched.final_url, capture=browser, resolver=resolver
        )
    except Exception as exc:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError

        if isinstance(exc, UnsafeBrowserTargetError):
            category = exc.category
        elif isinstance(exc, ValueError):
            category = "malformed"
        elif isinstance(exc, (PlaywrightTimeoutError, TimeoutError)):
            category = "timeout"
        elif isinstance(exc, TargetResolutionError):
            category = "transport"
        else:
            category = "transport"
        return AcquisitionFailure(category, str(exc), fetched.status, fetched.final_url)
    rendered = _extract(
        rendered_html,
        fetched=fetched,
        method_prefix="rendered",
        retrieved_at=retrieved_at,
    )
    if rendered is not None:
        return rendered
    return AcquisitionFailure(
        "empty_content",
        f"rendered content is shorter than {MIN_CONTENT_CHARS} characters",
        fetched.status,
        fetched.final_url,
    )
