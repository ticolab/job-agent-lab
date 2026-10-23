"""Pure evidence grounding and policy consistency validation."""

from __future__ import annotations

import re
import unicodedata

from job_eligibility.schema import EligibilityDecision, EligibilityResponse
from job_eligibility.version import POLICY_VERSION

COSTA_RICA_NAMES: tuple[str, ...] = (
    "Costa Rica",
    "Alajuela",
    "Cartago",
    "Guanacaste",
    "Heredia",
    "Puntarenas",
)
_CITIES_FOR_CR_ADJACENCY: tuple[str, ...] = (
    "Escazu",
    "Escazú",
    "San Jose",
    "San José",
)
_LATAM_NAMES: tuple[str, ...] = ("LATAM", "Latin America")


class UngroundedEvidenceError(ValueError):
    """Raised when a purported quote cannot be found in the posting."""

    def __init__(self, quote: str) -> None:
        self.quote = quote
        super().__init__(f"ungrounded evidence: {quote!r}")


def normalize(text: str) -> str:
    """Normalize text identically on both sides of evidence matching."""
    value = (
        unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
    )
    value = "\n".join(" ".join(line.split()) for line in value.split("\n"))
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    return value.casefold()


def _grounding_normalize(text: str) -> str:
    return " ".join(normalize(text).split())


def _contains_name(value: str, name: str) -> bool:
    return bool(
        re.search(rf"(?<!\w){re.escape(_grounding_normalize(name))}(?!\w)", value)
    )


def _is_costa_rica(value: str) -> bool:
    normalized = _grounding_normalize(value)
    if any(_contains_name(normalized, name) for name in COSTA_RICA_NAMES):
        return True
    adjacent = "|".join(
        re.escape(_grounding_normalize(name))
        for name in (*COSTA_RICA_NAMES[1:], *_CITIES_FOR_CR_ADJACENCY)
    )
    return bool(
        re.search(rf"(?:\bcr\b\W*(?:{adjacent})|(?:{adjacent})\W*\bcr\b)", normalized)
    )


def _is_latam(value: str) -> bool:
    normalized = _grounding_normalize(value)
    return any(_contains_name(normalized, name) for name in _LATAM_NAMES)


def _all_quotes(response: EligibilityResponse) -> list[str]:
    signals = response.signals
    return [
        *response.evidence,
        *signals.locations_named_evidence,
        *signals.remote_scope_evidence,
        *signals.exhaustive_country_list_evidence,
        *signals.work_authorization_evidence,
    ]


def _supports_eligible(response: EligibilityResponse) -> bool:
    signals = response.signals
    return (
        any(_is_costa_rica(location) for location in signals.locations_named)
        or signals.remote_scope in {"worldwide", "latam", "americas"}
        or (
            signals.exhaustive_country_list is not None
            and any(
                _is_costa_rica(country) for country in signals.exhaustive_country_list
            )
        )
    )


def _supports_ineligible(response: EligibilityResponse) -> bool:
    signals = response.signals
    exhaustive_excludes = signals.exhaustive_country_list is not None and not any(
        _is_costa_rica(country) for country in signals.exhaustive_country_list
    )
    has_costa_rica = any(
        _is_costa_rica(location) for location in signals.locations_named
    )
    has_latam = signals.remote_scope in {"latam", "americas"} or any(
        _is_latam(location) for location in signals.locations_named
    )
    named_elsewhere = (
        bool(signals.locations_named) and not has_costa_rica and not has_latam
    )
    authorization_elsewhere = any(
        not _is_costa_rica(country)
        for country in signals.work_authorization_required_in
    )
    return (
        exhaustive_excludes
        or signals.remote_scope == "other_region"
        or named_elsewhere
        or authorization_elsewhere
    )


def validate(response: EligibilityResponse, posting_text: str) -> EligibilityDecision:
    """Ground every quote and downgrade inconsistent verdicts to unknown."""
    posting = _grounding_normalize(posting_text)
    for quote in _all_quotes(response):
        if _grounding_normalize(quote) not in posting:
            raise UngroundedEvidenceError(quote)

    notes: list[str] = []
    decision = response.decision
    if decision == "eligible" and not _supports_eligible(response):
        notes.append("eligible decision is unsupported by the extracted signals")
        decision = "unknown"
    elif decision == "ineligible" and not _supports_ineligible(response):
        notes.append("ineligible decision is unsupported by the extracted signals")
        decision = "unknown"

    payload = response.model_dump()
    payload["decision"] = decision
    return EligibilityDecision(
        **payload,
        policy_version=POLICY_VERSION,
        grounded=True,
        validation_notes=notes,
    )
