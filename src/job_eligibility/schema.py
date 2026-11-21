"""Strict structured-output contract for one-shot Costa Rica eligibility.

The model-facing ``EligibilityResponse`` has exactly four fields and contains
no version, hash, confidence, fact, or rule fields. Its JSON Schema is accepted
by OpenAI strict structured output, which forbids a root-level union, so the
two accepted response shapes are enforced by ``validate.structural_errors``:

* missing-information unknown: ``decision`` is ``unknown``, ``reason`` is
  exactly ``MISSING_INFORMATION_REASON``, and both evidence lists are empty;
* every other response: one to five supporting excerpts and zero to five
  conflicting excerpts.

``EligibilityVerdict`` is the frozen result after structural validation and
normalized-exact grounding; only it crosses into persistence.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Decision = Literal["eligible", "ineligible", "unknown"]

MAX_EVIDENCE_ITEMS = 5
MAX_EVIDENCE_CHARACTERS = 300
MAX_REASON_CHARACTERS = 240
MISSING_INFORMATION_REASON = (
    "The posting states no role location, work arrangement, residence, "
    "or work authorization requirement."
)

EvidenceExcerpt = Annotated[
    str,
    Field(
        min_length=1,
        max_length=MAX_EVIDENCE_CHARACTERS,
        pattern=r".*\S.*",
        description=(
            "One short contiguous excerpt copied exactly from the posting, with no "
            "paraphrase, summary, prefix, commentary, quotation wrapper, or text "
            "combined from separate passages."
        ),
    ),
]
Reason = Annotated[
    str,
    Field(
        min_length=1,
        max_length=MAX_REASON_CHARACTERS,
        pattern=r"^\S[^\r\n]*[.!?]$",
        description=(
            "One concise sentence on a single line that explains the decision "
            "from the cited evidence and ends with a period."
        ),
    ),
]


class EligibilityResponse(BaseModel):
    """The model response before structural validation and grounding."""

    model_config = ConfigDict(extra="forbid")

    decision: Decision = Field(
        description=(
            "eligible retains the opportunity for a candidate who remains based in "
            "Costa Rica; ineligible means explicit wording excludes that candidate; "
            "unknown means the posting conflicts or does not say."
        )
    )
    reason: Reason
    supporting_evidence: Annotated[
        list[EvidenceExcerpt],
        Field(
            max_length=MAX_EVIDENCE_ITEMS,
            description=(
                "Exact excerpts that establish the decision. Empty only for the "
                "missing-information unknown response."
            ),
        ),
    ]
    conflicting_evidence: Annotated[
        list[EvidenceExcerpt],
        Field(
            max_length=MAX_EVIDENCE_ITEMS,
            description=(
                "Exact excerpts that point toward a different decision, including "
                "the opposing side of a conflict that makes the decision unknown. "
                "Empty when nothing opposes the decision."
            ),
        ),
    ]


class EligibilityVerdict(BaseModel):
    """A structurally valid, exact-grounded decision ready for persistence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Decision
    reason: str
    supporting_evidence: tuple[str, ...]
    conflicting_evidence: tuple[str, ...]
    eligibility_version: str
    prompt_hash: str
    grounded: Literal[True]
