from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from job_pipeline.content import (
    FAILURE_CATEGORIES,
    RETRYABLE_CATEGORIES,
    TERMINAL_CATEGORIES,
    AcquisitionFailure,
    hash_content,
)


def test_frame_methods_and_trace_equality() -> None:
    from job_pipeline.content.model import (
        AcquiredContent,
        AcquisitionTrace,
        FrameTrace,
    )

    content = AcquiredContent(
        "body",
        None,
        "https://example.com/job",
        200,
        "rendered_frame_main",
        None,
        None,
        datetime.now(UTC),
        hash_content("body"),
    )
    trace = AcquisitionTrace(frames_found=1)
    assert replace(content, trace=trace) == content
    assert (
        replace(content, method="rendered_frame_jsonld").method
        == "rendered_frame_jsonld"
    )
    with pytest.raises(ValueError, match="invalid acquisition method"):
        replace(content, method=cast(Any, "unknown"))
    failure = AcquisitionFailure("empty_content", "short")
    assert replace(failure, trace=trace) == failure
    assert FrameTrace(0, "about", "short", 3).element_id == ""
    assert FrameTrace(0, "about", "short", 3).element_title == ""


def test_failure_taxonomy_is_a_disjoint_complete_partition() -> None:
    assert RETRYABLE_CATEGORIES.isdisjoint(TERMINAL_CATEGORIES)
    assert RETRYABLE_CATEGORIES | TERMINAL_CATEGORIES == FAILURE_CATEGORIES
    assert {
        "not_found",
        "closed",
        "malformed",
        "off_site_redirect",
    } == TERMINAL_CATEGORIES


def test_failure_category_is_checked_at_runtime() -> None:
    with pytest.raises(ValueError, match="invalid failure category"):
        AcquisitionFailure(cast(Any, "unknown"), "bad")


def test_content_hash_is_stable_under_normalization() -> None:
    assert hash_content("Café\n\n\nBuild   APIs") == hash_content(
        "Cafe\u0301\n\nBuild APIs"
    )
    assert hash_content("one") != hash_content("two")
