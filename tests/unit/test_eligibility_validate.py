"""Tests for deterministic evidence grounding and policy consistency."""

from __future__ import annotations

import pytest

from job_discovery.domain.region import COSTA_RICA_LATAM
from job_eligibility import (
    COSTA_RICA_NAMES,
    EligibilityResponse,
    UngroundedEvidenceError,
    normalize,
    validate,
)
from job_pipeline.content.text import normalize as pipeline_normalize


def _response(
    decision: str,
    quote: str,
    *,
    locations: list[str] | None = None,
    scope: str = "none_stated",
    countries: list[str] | None = None,
    authorization: list[str] | None = None,
) -> EligibilityResponse:
    locations = locations or []
    authorization = authorization or []
    return EligibilityResponse.model_validate(
        {
            "decision": decision,
            "reason": "The cited location signal determines the result.",
            "evidence": [quote],
            "signals": {
                "locations_named": locations,
                "locations_named_evidence": [quote] if locations else [],
                "remote_scope": scope,
                "remote_scope_evidence": [quote] if scope != "none_stated" else [],
                "exhaustive_country_list": countries,
                "exhaustive_country_list_evidence": (
                    [quote] if countries is not None else []
                ),
                "work_authorization_required_in": authorization,
                "work_authorization_evidence": [quote] if authorization else [],
            },
        }
    )


def test_whitespace_case_and_nfkc_differences_still_ground() -> None:
    response = _response("eligible", "REMOTE IN COSTA RICA", locations=["Costa Rica"])
    decision = validate(response, "Remote\u00a0  in Costa Rica")
    assert decision.decision == "eligible"
    assert decision.grounded


def test_ungrounded_quote_raises_with_offending_quote() -> None:
    response = _response("unknown", "not in the posting")
    with pytest.raises(UngroundedEvidenceError) as exc_info:
        validate(response, "Remote")
    assert exc_info.value.quote == "not in the posting"


@pytest.mark.parametrize("scope", ["worldwide", "latam", "americas"])
def test_eligible_scopes_are_supported(scope: str) -> None:
    quote = f"Remote scope: {scope}"
    assert (
        validate(_response("eligible", quote, scope=scope), quote).decision
        == "eligible"
    )


def test_eligible_costa_rica_location_is_supported() -> None:
    quote = "Location: Heredia"
    decision = validate(_response("eligible", quote, locations=["Heredia"]), quote)
    assert decision.decision == "eligible"


def test_eligible_exhaustive_list_containing_costa_rica_is_supported() -> None:
    quote = "Available only in Canada and Costa Rica"
    response = _response("eligible", quote, countries=["Canada", "Costa Rica"])
    assert validate(response, quote).decision == "eligible"


def test_unsupported_eligible_is_forced_unknown() -> None:
    response = _response("eligible", "Remote")
    decision = validate(response, "Remote")
    assert decision.decision == "unknown"
    assert decision.validation_notes


def test_ineligible_exhaustive_list_excluding_costa_rica_is_supported() -> None:
    quote = "Available only in Canada and Mexico"
    response = _response("ineligible", quote, countries=["Canada", "Mexico"])
    assert validate(response, quote).decision == "ineligible"


def test_explicitly_empty_exhaustive_list_supports_ineligible() -> None:
    quote = "No countries are eligible"
    response = _response("ineligible", quote, countries=[])
    assert validate(response, quote).decision == "ineligible"


def test_ineligible_other_region_is_supported() -> None:
    quote = "Remote within Europe"
    response = _response("ineligible", quote, scope="other_region")
    assert validate(response, quote).decision == "ineligible"


def test_ineligible_named_location_elsewhere_is_supported() -> None:
    quote = "Onsite in Toronto, Canada"
    response = _response("ineligible", quote, locations=["Toronto, Canada"])
    assert validate(response, quote).decision == "ineligible"


def test_named_location_with_latam_signal_does_not_support_ineligible() -> None:
    quote = "Mexico preferred; remote throughout Latin America"
    response = _response("ineligible", quote, locations=["Mexico"], scope="latam")
    assert validate(response, quote).decision == "unknown"


def test_work_authorization_elsewhere_supports_ineligible() -> None:
    quote = "Must be authorized to work in Canada"
    response = _response("ineligible", quote, authorization=["Canada"])
    assert validate(response, quote).decision == "ineligible"


def test_unsupported_ineligible_is_forced_unknown() -> None:
    response = _response("ineligible", "Remote")
    assert validate(response, "Remote").decision == "unknown"


def test_bare_cr_is_not_costa_rica() -> None:
    quote = "Location: CR"
    response = _response("eligible", quote, locations=["CR"])
    assert validate(response, quote).decision == "unknown"


@pytest.mark.parametrize("location", ["CR - Heredia", "San José, CR"])
def test_cr_adjacent_to_costa_rica_place_is_costa_rica(location: str) -> None:
    quote = f"Location: {location}"
    response = _response("eligible", quote, locations=[location])
    assert validate(response, quote).decision == "eligible"


@pytest.mark.parametrize("ambiguous", ["San Jose", "Liberia", "Santa Ana", "Limon"])
def test_ambiguous_city_alone_is_not_costa_rica(ambiguous: str) -> None:
    quote = f"Location: {ambiguous}"
    response = _response("eligible", quote, locations=[ambiguous])
    assert validate(response, quote).decision == "unknown"


def test_normalize_contract() -> None:
    assert normalize("  CAFÉ\n\tRole  ") == "café\nrole"


def test_normalizer_agrees_with_pipeline_before_casefolding() -> None:
    samples = ("  CAFÉ\n\tRole  ", "Remote\u00a0  in Costa Rica", "A\n\n\nB")
    for sample in samples:
        assert normalize(sample) == pipeline_normalize(sample).casefold()


def test_costa_rica_names_match_discovery_unambiguous_vocabulary() -> None:
    assert COSTA_RICA_NAMES == (
        "Costa Rica",
        "Alajuela",
        "Cartago",
        "Guanacaste",
        "Heredia",
        "Puntarenas",
    )
    for name in COSTA_RICA_NAMES:
        assert COSTA_RICA_LATAM.matches(name)
    for ambiguous in ("San Jose", "Liberia", "Santa Ana", "Limon"):
        assert not COSTA_RICA_LATAM.matches(ambiguous)
