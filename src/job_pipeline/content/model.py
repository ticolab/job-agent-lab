"""Closed acquisition result types shared by every content rung."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

AcquisitionMethod = Literal[
    "jsonld", "main_content", "rendered_jsonld", "rendered_main"
]
FailureCategory = Literal[
    "not_found",
    "closed",
    "blocked",
    "timeout",
    "transport",
    "server_error",
    "empty_content",
    "off_site_redirect",
    "malformed",
]

FAILURE_CATEGORIES = frozenset(
    {
        "not_found",
        "closed",
        "blocked",
        "timeout",
        "transport",
        "server_error",
        "empty_content",
        "off_site_redirect",
        "malformed",
    }
)
TERMINAL_CATEGORIES = frozenset(
    {"not_found", "closed", "malformed", "off_site_redirect"}
)
RETRYABLE_CATEGORIES = FAILURE_CATEGORIES - TERMINAL_CATEGORIES


@dataclass(frozen=True, slots=True)
class AcquiredContent:
    text: str
    title: str | None
    resolved_url: str
    http_status: int
    method: AcquisitionMethod
    requisition_id: str | None
    jsonld: dict[str, Any] | None
    retrieved_at: datetime
    content_hash: str
    acquisition_html: str = ""

    def __post_init__(self) -> None:
        if self.method not in {
            "jsonld",
            "main_content",
            "rendered_jsonld",
            "rendered_main",
        }:
            raise ValueError(f"invalid acquisition method: {self.method}")


@dataclass(frozen=True, slots=True)
class AcquisitionFailure:
    category: FailureCategory
    message: str
    http_status: int | None = None
    resolved_url: str | None = None

    def __post_init__(self) -> None:
        if self.category not in FAILURE_CATEGORIES:
            raise ValueError(f"invalid failure category: {self.category}")


def hash_content(text: str) -> str:
    """Return the stable SHA-256 identity of normalized posting text."""
    from job_pipeline.content.text import normalize

    return hashlib.sha256(normalize(text).encode()).hexdigest()
