"""Failure classification, retry state, and work-selection policy tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from job_eligibility.validate import UngroundedEvidenceError
from job_pipeline.content.model import AcquisitionFailure, FailureCategory
from job_pipeline.llm import ModelCallError, ModelResponseError
from job_pipeline.policy import (
    ErrorCategory,
    PipelinePolicy,
    classify_failure,
    next_state,
    work_filter,
)


@dataclass(frozen=True)
class _Job:
    company_slug: str = "acme"
    url_key: str = "key-1"
    status: str = "new"
    attempt_count: int = 1
    next_attempt_after: datetime | None = None


@pytest.mark.parametrize(
    ("category", "retryable"),
    [
        ("not_found", False),
        ("closed", False),
        ("blocked", True),
        ("timeout", True),
        ("transport", True),
        ("server_error", True),
        ("empty_content", True),
        ("off_site_redirect", False),
        ("malformed", False),
    ],
)
def test_classify_acquisition_categories(category: str, retryable: bool) -> None:
    failure = AcquisitionFailure(cast(FailureCategory, category), "failure")
    assert classify_failure(failure) == (category, retryable)


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (ModelCallError("model", "down"), ("model_call", True)),
        (ModelResponseError("model", "bad"), ("validation_failed", True)),
        (UngroundedEvidenceError("quote"), ("ungrounded_evidence", True)),
        (TimeoutError(), ("timeout", True)),
        (RuntimeError("unexpected"), ("internal", True)),
    ],
)
def test_classify_exception_categories(
    failure: BaseException, expected: tuple[str, bool]
) -> None:
    assert classify_failure(failure) == expected


@pytest.mark.parametrize(
    "category", ["not_found", "closed", "malformed", "off_site_redirect"]
)
def test_terminal_acquisition_failure_is_unavailable(category: str) -> None:
    status, retry_at = next_state(
        _Job(attempt_count=1),
        cast(ErrorCategory, category),
        False,
        PipelinePolicy(),
    )
    assert (status, retry_at) == ("unavailable", None)


def test_retry_backoff_uses_claimed_attempt_count() -> None:
    before = datetime.now(UTC).replace(tzinfo=None)
    status, retry_at = next_state(
        _Job(attempt_count=2),
        "transport",
        True,
        PipelinePolicy(retry_backoff=timedelta(minutes=5)),
    )
    after = datetime.now(UTC).replace(tzinfo=None)
    assert status == "retry"
    assert retry_at is not None
    assert before + timedelta(minutes=10) <= retry_at <= after + timedelta(minutes=10)


@pytest.mark.parametrize(
    ("category", "attempt_count", "expected"),
    [
        ("transport", 2, "retry"),
        ("transport", 3, "failed"),
        ("internal", 1, "retry"),
        ("internal", 2, "failed"),
    ],
)
def test_retry_limit(category: str, attempt_count: int, expected: str) -> None:
    status, _ = next_state(
        _Job(attempt_count=attempt_count),
        cast(ErrorCategory, category),
        True,
        PipelinePolicy(retry_limit=3),
    )
    assert status == expected


def test_work_filter_requires_current_snapshot_and_due_retry() -> None:
    now = datetime(2026, 1, 2)
    snapshot = frozenset({("acme", "key-1")})
    assert work_filter(_Job(), snapshot, now)
    assert work_filter(
        _Job(status="retry", next_attempt_after=now - timedelta(seconds=1)),
        snapshot,
        now,
    )
    assert not work_filter(_Job(url_key="gone"), snapshot, now)
    assert not work_filter(_Job(status="retry", next_attempt_after=None), snapshot, now)
    assert not work_filter(
        _Job(status="retry", next_attempt_after=now + timedelta(seconds=1)),
        snapshot,
        now,
    )
    assert work_filter(
        _Job(status="failed", url_key="gone"), snapshot, now, reprocess=True
    )
