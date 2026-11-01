"""Deterministic posting-content acquisition."""

from job_pipeline.content.model import (
    FAILURE_CATEGORIES,
    RETRYABLE_CATEGORIES,
    TERMINAL_CATEGORIES,
    AcquiredContent,
    AcquisitionFailure,
    hash_content,
)

__all__ = [
    "FAILURE_CATEGORIES",
    "RETRYABLE_CATEGORIES",
    "TERMINAL_CATEGORIES",
    "AcquiredContent",
    "AcquisitionFailure",
    "hash_content",
]
