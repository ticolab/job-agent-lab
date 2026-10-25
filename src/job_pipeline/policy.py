"""Operational policy for pipeline planning, retries, and failures."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from job_discovery.persistence.timestamps import as_naive_utc, utc_now
from job_eligibility.validate import UngroundedEvidenceError as EligibilityEvidenceError
from job_extractor.validate import UngroundedEvidenceError as ExtractionEvidenceError
from job_pipeline.content.model import (
    RETRYABLE_CATEGORIES,
    TERMINAL_CATEGORIES,
    AcquisitionFailure,
)
from job_pipeline.llm import ModelCallError, ModelResponseError
from job_pipeline.settings import (
    DEFAULT_BROWSER_CONCURRENCY,
    DEFAULT_HTTP_CONCURRENCY,
    DEFAULT_JOB_TIMEOUT,
    DEFAULT_MODEL,
    DEFAULT_MODEL_CONCURRENCY,
    DEFAULT_PER_HOST_CONCURRENCY,
    RETRY_BACKOFF,
    RETRY_LIMIT,
    STALE_ATTEMPT_PHASES,
)

ErrorCategory = Literal[
    "not_found",
    "closed",
    "blocked",
    "timeout",
    "transport",
    "server_error",
    "empty_content",
    "off_site_redirect",
    "malformed",
    "model_call",
    "validation_failed",
    "ungrounded_evidence",
    "internal",
]


class WorkItem(Protocol):
    @property
    def company_slug(self) -> str: ...

    @property
    def url_key(self) -> str: ...

    @property
    def status(self) -> str: ...

    @property
    def attempt_count(self) -> int: ...

    @property
    def next_attempt_after(self) -> datetime | None: ...


@dataclass(frozen=True, slots=True)
class PipelinePolicy:
    """The tunables one pipeline run executes under.

    ``job_timeout`` bounds each network or model step of a job once that
    step holds its concurrency slot; queue time is never charged against it.
    ``in_flight`` is how many jobs may be claimed at once, so an attempt's
    age stays close to its active time and the reaper's bound,
    :attr:`stale_after`, means what it says.
    """

    http_concurrency: int = DEFAULT_HTTP_CONCURRENCY
    browser_concurrency: int = DEFAULT_BROWSER_CONCURRENCY
    model_concurrency: int = DEFAULT_MODEL_CONCURRENCY
    per_host_concurrency: int = DEFAULT_PER_HOST_CONCURRENCY
    job_timeout: float = DEFAULT_JOB_TIMEOUT
    retry_limit: int = RETRY_LIMIT
    retry_backoff: timedelta = RETRY_BACKOFF
    limit: int | None = None
    model: str = DEFAULT_MODEL

    @property
    def in_flight(self) -> int:
        """Jobs claimed at once: enough to keep every slot busy, no more."""
        return self.http_concurrency + self.model_concurrency

    @property
    def stale_after(self) -> timedelta:
        """Age past which an unfinished attempt cannot be live."""
        return timedelta(seconds=self.job_timeout * STALE_ATTEMPT_PHASES)


def classify_failure(
    failure: BaseException | AcquisitionFailure,
) -> tuple[ErrorCategory, bool]:
    if isinstance(failure, AcquisitionFailure):
        return failure.category, failure.category in RETRYABLE_CATEGORIES
    if isinstance(failure, ModelCallError):
        return "model_call", True
    if isinstance(failure, ModelResponseError):
        return "validation_failed", True
    if isinstance(failure, (EligibilityEvidenceError, ExtractionEvidenceError)):
        return "ungrounded_evidence", True
    if isinstance(failure, TimeoutError):
        return "timeout", True
    return "internal", True


def next_state(
    job: WorkItem,
    category: ErrorCategory,
    retryable: bool,
    policy: PipelinePolicy,
) -> tuple[str, datetime | None]:
    if category in TERMINAL_CATEGORIES:
        return "unavailable", None
    retry_limit = (
        min(policy.retry_limit, 2) if category == "internal" else policy.retry_limit
    )
    if retryable and job.attempt_count < retry_limit:
        return "retry", utc_now() + policy.retry_backoff * job.attempt_count
    return "failed", None


def work_filter(
    job: WorkItem,
    snapshot_keys: Collection[tuple[str, str]],
    now: datetime,
    *,
    reprocess: bool = False,
) -> bool:
    if reprocess:
        return True
    if (job.company_slug, job.url_key) not in snapshot_keys:
        return False
    if job.status == "new":
        return True
    if job.status != "retry" or job.next_attempt_after is None:
        return False
    return job.next_attempt_after <= as_naive_utc(now)


__all__ = [
    "ErrorCategory",
    "PipelinePolicy",
    "WorkItem",
    "classify_failure",
    "next_state",
    "work_filter",
]
