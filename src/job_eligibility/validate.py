"""Structural validation and normalized-exact grounding of one-shot verdicts.

Grounding proves each excerpt occurs in the posting; it does not prove the
model interpreted the excerpt correctly. Semantic review stays with reviewed
evidence obligations, replay evaluation, and manual reason review.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from job_eligibility.normalize import grounding_text, normalize_text
from job_eligibility.schema import (
    MISSING_INFORMATION_REASON,
    EligibilityResponse,
    EligibilityVerdict,
)
from job_eligibility.version import ELIGIBILITY_PROMPT_HASH, ELIGIBILITY_VERSION

EvidenceBucket = Literal["supporting_evidence", "conflicting_evidence"]


@dataclass(frozen=True, slots=True)
class GroundingMiss:
    """A safe grounding diagnostic: path, category, hash, and length only."""

    path: str
    reason: Literal["not_in_posting"]
    value_sha256: str
    value_characters: int


class UngroundedEvidenceError(ValueError):
    """Raised when a purported excerpt cannot be found in the posting."""

    def __init__(self, quote: str) -> None:
        self.quote = quote
        super().__init__("ungrounded evidence")


class EligibilityValidationError(ValueError):
    """Raised when a schema-valid response violates a structural invariant."""


def normalize(text: str) -> str:
    """Normalize text identically on both sides of evidence matching."""
    return normalize_text(text)


def is_missing_information(response: EligibilityResponse) -> bool:
    """Whether the response uses the canonical empty-evidence unknown form."""
    return (
        response.decision == "unknown"
        and response.reason == MISSING_INFORMATION_REASON
        and not response.supporting_evidence
        and not response.conflicting_evidence
    )


def _evidence(response: EligibilityResponse) -> Iterator[tuple[str, str]]:
    buckets: tuple[EvidenceBucket, ...] = (
        "supporting_evidence",
        "conflicting_evidence",
    )
    for bucket in buckets:
        for index, quote in enumerate(getattr(response, bucket)):
            yield f"$.{bucket}[{index}]", quote


def structural_errors(response: EligibilityResponse) -> tuple[str, ...]:
    """Return every structural violation; an empty tuple means consistent.

    The two accepted shapes are the canonical missing-information unknown and
    an evidenced response with at least one supporting excerpt. Duplicates and
    cross-bucket overlap compare grounding-normalized text, so whitespace or
    layout variants of one excerpt cannot be counted twice.
    """
    errors: list[str] = []
    no_evidence = not response.supporting_evidence
    if no_evidence:
        if response.conflicting_evidence:
            errors.append("conflicting evidence requires supporting evidence")
        if response.decision != "unknown":
            errors.append(f"{response.decision} requires supporting evidence")
        if response.reason != MISSING_INFORMATION_REASON:
            errors.append("empty evidence requires the missing-information reason")
    elif response.reason == MISSING_INFORMATION_REASON:
        errors.append("the missing-information reason forbids evidence")
    seen: dict[str, str] = {}
    for path, quote in _evidence(response):
        key = grounding_text(quote)
        bucket = path.split("[", 1)[0]
        previous = seen.get(key)
        if previous is None:
            seen[key] = bucket
        elif previous == bucket:
            errors.append(f"{path} duplicates an earlier excerpt")
        else:
            errors.append(f"{path} appears in both evidence buckets")
    return tuple(errors)


def _miss(path: str, value: str) -> GroundingMiss:
    return GroundingMiss(
        path=path,
        reason="not_in_posting",
        value_sha256=hashlib.sha256(value.encode()).hexdigest(),
        value_characters=len(value),
    )


def collect_grounding_misses(
    response: EligibilityResponse, posting_text: str
) -> list[GroundingMiss]:
    """Return a safe diagnostic for every excerpt absent from the posting."""
    posting = grounding_text(posting_text)
    misses: list[GroundingMiss] = []
    for path, quote in _evidence(response):
        normalized = grounding_text(quote)
        if not normalized or normalized not in posting:
            misses.append(_miss(path, quote))
    return misses


def validate(response: EligibilityResponse, posting_text: str) -> EligibilityVerdict:
    """Reject structurally invalid or ungrounded responses; freeze the rest."""
    errors = structural_errors(response)
    if errors:
        raise EligibilityValidationError("; ".join(errors))
    posting = grounding_text(posting_text)
    for _, quote in _evidence(response):
        normalized = grounding_text(quote)
        if not normalized or normalized not in posting:
            raise UngroundedEvidenceError(quote)
    return EligibilityVerdict(
        decision=response.decision,
        reason=response.reason,
        supporting_evidence=tuple(response.supporting_evidence),
        conflicting_evidence=tuple(response.conflicting_evidence),
        eligibility_version=ELIGIBILITY_VERSION,
        prompt_hash=ELIGIBILITY_PROMPT_HASH,
        grounded=True,
    )
