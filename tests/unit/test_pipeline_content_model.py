from __future__ import annotations

from typing import Any, cast

import pytest

from job_pipeline.content import (
    FAILURE_CATEGORIES,
    RETRYABLE_CATEGORIES,
    TERMINAL_CATEGORIES,
    AcquisitionFailure,
    hash_content,
)


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
