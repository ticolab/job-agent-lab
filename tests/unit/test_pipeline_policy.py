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
    MissingCheckpointError,
    PipelinePolicy,
    classify_failure,
    next_state,
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


@pytest.mark.parametrize("attempt_count", [0, 1, 2, 3, 100])
@pytest.mark.parametrize("retryable", [False, True])
def test_missing_checkpoint_fails_immediately(
    attempt_count: int, retryable: bool
) -> None:
    assert classify_failure(MissingCheckpointError("content missing")) == (
        "missing_checkpoint",
        False,
    )
    assert next_state(
        _Job(attempt_count=attempt_count),
        "missing_checkpoint",
        retryable,
        PipelinePolicy(),
    ) == ("failed", None)


def test_claim_ceilings() -> None:
    policy = PipelinePolicy(http_concurrency=7, model_concurrency=3)
    assert policy.claim_ceiling("eligibility") == 10
    assert policy.claim_ceiling("extraction") == 3
