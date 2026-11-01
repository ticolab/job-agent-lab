"""Ordered deterministic posting-content acquisition ladder."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from time import monotonic

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
    AcquisitionTrace,
    FrameOutcome,
    FrameTrace,
    StepTrace,
    hash_content,
)
from job_pipeline.content.render import (
    RenderedPage,
    UnsafeBrowserTargetError,
    render_page,
)
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


@dataclass(frozen=True, slots=True)
class _Evaluation:
    html: str
    posting: JobPostingLd | None
    main: str
    title: str | None


def _evaluate(html: str, url: str) -> _Evaluation:
    main, title = main_text(html, url)
    return _Evaluation(html, _best_posting(extract_job_postings(html)), main, title)


def _closed_failure(
    message: str, fetched: FetchResult, *, resolved_url: str | None = None
) -> AcquisitionFailure:
    return AcquisitionFailure(
        "closed", message, fetched.status, resolved_url or fetched.final_url
    )


def _decide(
    evaluation: _Evaluation,
    *,
    fetched: FetchResult,
    method: AcquisitionMethod,
    retrieved_at: datetime,
    corroborating_body: str,
) -> AcquiredContent | AcquisitionFailure | None:
    posting, main, page_title = evaluation.posting, evaluation.main, evaluation.title
    if (
        posting is not None
        and posting.is_closed()
        and (
            looks_closed(main)
            or looks_closed(corroborating_body)
            or len(corroborating_body) < MIN_CONTENT_CHARS
        )
    ):
        return _closed_failure("JSON-LD validThrough is in the past", fetched)

    if posting is not None and len(posting.description_text) >= MIN_CONTENT_CHARS:
        text = _deduplicate_paragraphs(posting.description_text, main)
        title = posting.title or page_title
    elif len(main) >= MIN_CONTENT_CHARS:
        text = main
        if method == "jsonld":
            method = "main_content"
        elif method == "rendered_jsonld":
            method = "rendered_main"
        elif method == "rendered_frame_jsonld":
            method = "rendered_frame_main"
        title = page_title or (posting.title if posting is not None else None)
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
        requisition_id=posting.identifier if posting is not None else None,
        jsonld=posting.raw if posting is not None else None,
        retrieved_at=retrieved_at,
        content_hash=hash_content(text),
        acquisition_html=evaluation.html,
    )


def _extract(
    html: str,
    *,
    fetched: FetchResult,
    method_prefix: str,
    retrieved_at: datetime,
) -> AcquiredContent | AcquisitionFailure | None:
    evaluation = _evaluate(html, fetched.final_url)
    return _decide(
        evaluation,
        fetched=fetched,
        method="rendered_jsonld" if method_prefix else "jsonld",
        retrieved_at=retrieved_at,
        corroborating_body=evaluation.main,
    )


def _select_rendered(
    page: RenderedPage,
    *,
    fetched: FetchResult,
    retrieved_at: datetime,
) -> tuple[
    AcquiredContent | AcquisitionFailure | None, tuple[FrameTrace, ...], int | None
]:
    """Page success/closure wins; fallback ranks JSON-LD, length, then element order.

    A selected frame alone must qualify. Its own body corroborates its dates;
    only an open qualifying frame can corroborate the page's stale validThrough.
    """
    evaluation = _evaluate(page.html, page.url)
    traces: list[FrameTrace] = []
    candidates: list[tuple[AcquiredContent, int, _Evaluation]] = []
    closed: AcquisitionFailure | None = None
    for frame in page.frames:
        if frame.outcome != "candidate":
            traces.append(
                FrameTrace(
                    frame.index,
                    frame.origin,
                    frame.outcome,
                    0,
                    frame.element_id,
                    frame.element_title,
                )
            )
            continue
        own = _evaluate(frame.html, frame.url)
        result = _decide(
            own,
            fetched=fetched,
            method="rendered_frame_jsonld",
            retrieved_at=retrieved_at,
            corroborating_body=own.main,
        )
        if result is None and looks_closed(own.main):
            result = _closed_failure(
                "posting text indicates the job is closed", fetched
            )
        chars = (
            len(result.text) if isinstance(result, AcquiredContent) else len(own.main)
        )
        outcome: FrameOutcome = "short"
        if isinstance(result, AcquiredContent):
            candidates.append((result, frame.index, own))
            outcome = "qualifying"
        elif isinstance(result, AcquisitionFailure):
            closed = closed or result
            outcome = "closed"
        traces.append(
            FrameTrace(
                frame.index,
                frame.origin,
                outcome,
                chars,
                frame.element_id,
                frame.element_title,
            )
        )

    chosen = max(
        candidates,
        key=lambda item: (
            item[0].method == "rendered_frame_jsonld",
            len(item[0].text),
            -item[1],
        ),
        default=None,
    )
    page_result = _decide(
        evaluation,
        fetched=fetched,
        method="rendered_jsonld",
        retrieved_at=retrieved_at,
        corroborating_body=chosen[0].text if chosen else evaluation.main,
    )
    if page_result is not None:
        return page_result, tuple(traces), None
    if chosen is None:
        return closed, tuple(traces), None
    content, index, own = chosen
    text = _deduplicate_paragraphs(evaluation.main, content.text)
    if looks_closed(text):
        return (
            _closed_failure("posting text indicates the job is closed", fetched),
            tuple(traces),
            None,
        )
    posting = own.posting or evaluation.posting
    title = (
        (own.posting.title if own.posting else None)
        or (evaluation.posting.title if evaluation.posting else None)
        or evaluation.title
        or own.title
    )
    content = replace(
        content,
        text=text,
        title=title,
        content_hash=hash_content(text),
        requisition_id=posting.identifier if posting else None,
        jsonld=posting.raw if posting else None,
        acquisition_html=page.html,
        frame_html=own.html,
    )
    traces = [
        replace(trace, outcome="selected") if trace.index == index else trace
        for trace in traces
    ]
    return content, tuple(traces), index


def _extract_rendered(
    page: RenderedPage,
    *,
    fetched: FetchResult,
    retrieved_at: datetime,
) -> AcquiredContent | AcquisitionFailure | None:
    return _select_rendered(page, fetched=fetched, retrieved_at=retrieved_at)[0]


def _step(
    step: str,
    html: str,
    url: str | None,
    status: int | None,
    started: float,
) -> StepTrace:
    evaluation = _evaluate(html, url or "")
    return StepTrace(
        "http" if step == "http" else "render",
        status,
        url,
        len(evaluation.main),
        evaluation.posting is not None,
        monotonic() - started,
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
    steps: list[StepTrace] = []
    frames: tuple[FrameTrace, ...] = ()
    frames_found = 0
    selected: int | None = None

    def finish(
        result: AcquiredContent | AcquisitionFailure,
    ) -> AcquiredContent | AcquisitionFailure:
        return replace(
            result, trace=AcquisitionTrace(tuple(steps), frames_found, frames, selected)
        )

    started = monotonic()
    fetched = await fetch(
        url,
        client=http,
        job_board_url=company.job_board_url,
        resolver=resolver,
    )
    if isinstance(fetched, AcquisitionFailure):
        steps.append(
            _step("http", "", fetched.resolved_url, fetched.http_status, started)
        )
        return finish(fetched)
    steps.append(
        _step("http", fetched.html, fetched.final_url, fetched.status, started)
    )
    retrieved_at = datetime.now(UTC)
    if not force_rendered:
        plain = _extract(
            fetched.html, fetched=fetched, method_prefix="", retrieved_at=retrieved_at
        )
        if plain is not None:
            return finish(plain)
    if browser is None:
        return finish(
            AcquisitionFailure(
                "empty_content",
                f"content is shorter than {MIN_CONTENT_CHARS} characters",
                fetched.status,
                fetched.final_url,
            )
        )

    def capture_frames_if(html: str, page_url: str) -> bool:
        evaluation = _evaluate(html, page_url)
        posting = evaluation.posting
        if (
            posting is not None
            and posting.is_closed()
            and len(evaluation.main) < MIN_CONTENT_CHARS
            and not looks_closed(evaluation.main)
            and not looks_closed(posting.description_text)
        ):
            return True
        return (
            _decide(
                evaluation,
                fetched=fetched,
                method="rendered_jsonld",
                retrieved_at=retrieved_at,
                corroborating_body=evaluation.main,
            )
            is None
        )

    started = monotonic()
    try:
        page = await render_page(
            fetched.final_url,
            capture=browser,
            resolver=resolver,
            capture_frames_if=capture_frames_if,
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
        steps.append(_step("render", "", fetched.final_url, fetched.status, started))
        return finish(
            AcquisitionFailure(category, str(exc), fetched.status, fetched.final_url)
        )
    steps.append(_step("render", page.html, page.url, fetched.status, started))
    frames_found = page.frames_found
    rendered, frames, selected = _select_rendered(
        page, fetched=fetched, retrieved_at=retrieved_at
    )
    if rendered is not None:
        return finish(rendered)
    return finish(
        AcquisitionFailure(
            "empty_content",
            f"rendered content is shorter than {MIN_CONTENT_CHARS} characters",
            fetched.status,
            fetched.final_url,
        )
    )
