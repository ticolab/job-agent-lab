"""Structural validation and word-level grounding of one-shot verdicts.

Grounding proves each excerpt's words occur in the posting as one unbroken run;
it does not prove the model interpreted the excerpt correctly. Semantic review
stays with reviewed evidence obligations, replay evaluation, and manual reason
review.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from job_eligibility.normalize import locate_words, word_key, word_spans
from job_eligibility.schema import (
    MISSING_INFORMATION_REASON,
    EligibilityResponse,
    EligibilityVerdict,
)
from job_eligibility.version import ELIGIBILITY_PROMPT_HASH

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
    an evidenced response with at least one supporting excerpt. An excerpt cited
    in both buckets is a violation, compared by its words. A repeat inside one
    bucket is harmless and is merged by ``validate`` instead of rejected.
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
        key = word_key(quote)
        bucket = path.split("[", 1)[0]
        previous = seen.setdefault(key, bucket)
        if previous != bucket:
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
    posting = word_spans(posting_text)
    return [
        _miss(path, quote)
        for path, quote in _evidence(response)
        if locate_words(quote, posting) is None
    ]


def _posting_excerpts(
    quotes: list[str], posting_text: str, posting: list[tuple[str, int, int]]
) -> tuple[str, ...]:
    """Return each quote as the posting's own text, merging repeats in order."""
    excerpts: dict[tuple[int, int], str] = {}
    for quote in quotes:
        span = locate_words(quote, posting)
        if span is None:
            raise UngroundedEvidenceError(quote)
        excerpts.setdefault(span, posting_text[span[0] : span[1]])
    return tuple(excerpts.values())


def validate(response: EligibilityResponse, posting_text: str) -> EligibilityVerdict:
    """Reject structurally invalid or ungrounded responses; freeze the rest.

    The verdict stores the posting's own characters for each excerpt, not the
    model's copy, so persisted evidence is always literal posting text.
    """
    errors = structural_errors(response)
    if errors:
        raise EligibilityValidationError("; ".join(errors))
    posting = word_spans(posting_text)
    return EligibilityVerdict(
        decision=response.decision,
        reason=response.reason,
        supporting_evidence=_posting_excerpts(
            response.supporting_evidence, posting_text, posting
        ),
        conflicting_evidence=_posting_excerpts(
            response.conflicting_evidence, posting_text, posting
        ),
        prompt_hash=ELIGIBILITY_PROMPT_HASH,
        grounded=True,
    )
