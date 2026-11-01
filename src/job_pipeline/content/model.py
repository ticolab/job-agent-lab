"""Closed acquisition result types shared by every content rung."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

AcquisitionMethod = Literal[
    "jsonld",
    "main_content",
    "rendered_jsonld",
    "rendered_main",
    "rendered_frame_jsonld",
    "rendered_frame_main",
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


FrameOrigin = Literal["same_origin", "about", "cross_origin"]
FrameOutcome = Literal[
    "selected",
    "qualifying",
    "short",
    "closed",
    "hidden",
    "too_small",
    "cross_origin",
    "sandboxed",
    "unreadable",
    "over_limit",
]


@dataclass(frozen=True, slots=True)
class StepTrace:
    step: Literal["http", "render"]
    http_status: int | None
    resolved_url: str | None
    page_chars: int
    has_jsonld: bool
    elapsed_s: float


@dataclass(frozen=True, slots=True)
class FrameTrace:
    index: int
    origin: FrameOrigin
    outcome: FrameOutcome
    chars: int
    element_id: str = ""
    element_title: str = ""


@dataclass(frozen=True, slots=True)
class AcquisitionTrace:
    steps: tuple[StepTrace, ...] = ()
    frames_found: int = 0
    frames: tuple[FrameTrace, ...] = ()
    selected_frame: int | None = None


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
    frame_html: str = ""
    trace: AcquisitionTrace | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if self.method not in {
            "jsonld",
            "main_content",
            "rendered_jsonld",
            "rendered_main",
            "rendered_frame_jsonld",
            "rendered_frame_main",
        }:
            raise ValueError(f"invalid acquisition method: {self.method}")


@dataclass(frozen=True, slots=True)
class AcquisitionFailure:
    category: FailureCategory
    message: str
    http_status: int | None = None
    resolved_url: str | None = None
    trace: AcquisitionTrace | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if self.category not in FAILURE_CATEGORIES:
            raise ValueError(f"invalid failure category: {self.category}")


def hash_content(text: str) -> str:
    """Return the stable SHA-256 identity of normalized posting text."""
    from job_pipeline.content.text import normalize

    return hashlib.sha256(normalize(text).encode()).hexdigest()
