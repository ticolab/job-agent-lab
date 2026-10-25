from __future__ import annotations

import re
import unicodedata
from typing import Any, cast

from job_extractor.normalize import normalize
from job_extractor.schema import (
    SCHEMA_VERSION,
    ExtractionResponse,
    Fact,
    JobResult,
    ModelFact,
    Qualification,
    TechnologyMention,
)
from job_extractor.version import EXTRACTOR_VERSION

_FACT_FIELDS = tuple(ExtractionResponse.model_fields)
_REQUIRED_CUE = re.compile(
    r"\b(required|must|essential|minimum|mandatory|need|should have)\b", re.I
)
_CAPITALIZED_TOKEN = re.compile(r"\b[A-Z][A-Za-z0-9.+#-]*\b")
_REQUIRED_HEADING = re.compile(
    r"^(requirements|required qualifications|minimum qualifications|must have)s?:?$",
    re.I,
)
_PREFERRED_HEADING = re.compile(
    r"^(preferred qualifications|nice to have|bonus qualifications)s?:?$", re.I
)


class UngroundedEvidenceError(ValueError):
    def __init__(self, quotes: list[str]) -> None:
        self.quotes = quotes
        super().__init__(f"response contains ungrounded evidence: {quotes!r}")


def _normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _fact_evidence(fact: ModelFact[Any]) -> list[str]:
    evidence = list(fact.evidence)
    value = fact.value
    if isinstance(value, list):
        for item in value:
            if isinstance(item, (Qualification, TechnologyMention)):
                evidence.extend(item.evidence)
    return evidence


def _validated_fact(
    fact: ModelFact[Any], posting_text: str
) -> tuple[Fact[Any], list[str]]:
    normalized_posting = _normalized(posting_text)
    ungrounded = [
        quote
        for quote in _fact_evidence(fact)
        if _normalized(quote) not in normalized_posting
    ]
    if ungrounded:
        return Fact(state="failed", value=None, evidence=[]), ungrounded
    return (
        Fact.model_validate(
            {"state": fact.state, "value": fact.value, "evidence": fact.evidence}
        ),
        ungrounded,
    )


def _proper_nouns(summary: str) -> list[str]:
    nouns: list[str] = []
    for match in _CAPITALIZED_TOKEN.finditer(summary):
        prefix = summary[: match.start()].rstrip()
        if not prefix or prefix[-1] in ".!?":
            continue
        nouns.append(match.group())
    return nouns


def _in_required_section(quote: str, posting_text: str) -> bool:
    quote_key = _normalized(quote)
    section: str | None = None
    for line in posting_text.splitlines():
        stripped = line.strip()
        if _REQUIRED_HEADING.fullmatch(stripped):
            section = "required"
        elif _PREFERRED_HEADING.fullmatch(stripped):
            section = "preferred"
        if quote_key and quote_key in _normalized(stripped):
            return section == "required"
    return False


def _is_required_trace(
    mention: TechnologyMention,
    required: Fact[list[Qualification]],
    posting_text: str,
) -> bool:
    qualification_evidence = {
        _normalized(quote)
        for qualification in required.value or []
        for quote in qualification.evidence
    }
    return any(
        _normalized(quote) in qualification_evidence
        or _REQUIRED_CUE.search(quote)
        or _in_required_section(quote, posting_text)
        for quote in mention.evidence
    )


def validate(response: ExtractionResponse, posting_text: str) -> JobResult:
    facts: dict[str, Fact[Any]] = {}
    notes: list[str] = []
    failed_extracted: list[str] = []
    extracted_count = 0

    for field_name in _FACT_FIELDS:
        model_fact = cast(ModelFact[Any], getattr(response, field_name))
        if model_fact.state == "extracted":
            extracted_count += 1
        fact, ungrounded = _validated_fact(model_fact, posting_text)
        facts[field_name] = fact
        if ungrounded:
            if model_fact.state == "extracted":
                failed_extracted.append(field_name)
            for quote in ungrounded:
                notes.append(f'{field_name}: ungrounded evidence "{quote}"')

    if facts["title"].state == "failed" or (
        extracted_count and len(failed_extracted) > extracted_count / 3
    ):
        quotes = [
            quote
            for field_name in failed_extracted
            for quote in _fact_evidence(
                cast(ModelFact[Any], getattr(response, field_name))
            )
            if _normalized(quote) not in _normalized(posting_text)
        ]
        raise UngroundedEvidenceError(quotes)

    location = facts["location"]
    if location.state == "extracted" and facts["location_text"].state != "extracted":
        facts["location"] = Fact(state="failed", value=None, evidence=[])
        notes.append("location: extracted location requires extracted location_text")

    required = cast(Fact[list[Qualification]], facts["required_qualifications"])
    preferred = cast(Fact[list[Qualification]], facts["preferred_qualifications"])
    required_texts = {_normalized(item.text) for item in required.value or []}
    duplicates = [
        item.text
        for item in preferred.value or []
        if _normalized(item.text) in required_texts
    ]
    if duplicates:
        facts["preferred_qualifications"] = Fact(
            state="failed", value=None, evidence=[]
        )
        notes.append(
            "preferred_qualifications: duplicates required qualification: "
            + ", ".join(duplicates)
        )

    summary = cast(Fact[str], facts["summary"])
    if summary.state == "extracted" and summary.value is not None:
        missing_nouns = [
            noun
            for noun in _proper_nouns(summary.value)
            if _normalized(noun) not in _normalized(posting_text)
        ]
        if missing_nouns:
            facts["summary"] = Fact(
                state="uncertain", value=summary.value, evidence=summary.evidence
            )
            notes.append(
                "summary: proper nouns absent from posting: " + ", ".join(missing_nouns)
            )

    technologies = cast(Fact[list[TechnologyMention]], facts["technologies"])
    adjusted_mentions: list[TechnologyMention] = []
    for mention in technologies.value or []:
        if mention.status == "required" and not _is_required_trace(
            mention, required, posting_text
        ):
            adjusted_mentions.append(mention.model_copy(update={"status": "mentioned"}))
            notes.append(
                f"technologies: lowered {mention.mention} from required to mentioned"
            )
        else:
            adjusted_mentions.append(mention)
    if technologies.value is not None:
        facts["technologies"] = Fact(
            state=technologies.state,
            value=adjusted_mentions,
            evidence=technologies.evidence,
        )

    return JobResult(
        **facts,
        technologies_normalized=normalize(adjusted_mentions),
        extractor_version=EXTRACTOR_VERSION,
        schema_version=SCHEMA_VERSION,
        validation_notes=notes,
    )
