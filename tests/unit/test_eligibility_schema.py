"""Tests for the strict eligibility structured-output contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from job_eligibility import EligibilityDecision, EligibilityResponse, LocationSignals

SCHEMA_GOLDEN = (
    Path(__file__).resolve().parents[1] / "fixtures" / "schemas" / "eligibility.json"
)


def _signals() -> LocationSignals:
    return LocationSignals(
        locations_named=["Costa Rica"],
        locations_named_evidence=["Remote in Costa Rica"],
        remote_scope="none_stated",
        remote_scope_evidence=[],
        exhaustive_country_list=None,
        exhaustive_country_list_evidence=[],
        work_authorization_required_in=[],
        work_authorization_evidence=[],
    )


def test_response_round_trip() -> None:
    response = EligibilityResponse(
        decision="eligible",
        reason="The role explicitly permits work from Costa Rica.",
        evidence=["Remote in Costa Rica"],
        signals=_signals(),
    )
    assert (
        EligibilityResponse.model_validate_json(response.model_dump_json()) == response
    )


def test_decision_is_frozen() -> None:
    decision = EligibilityDecision(
        decision="eligible",
        reason="The role explicitly permits work from Costa Rica.",
        evidence=["Remote in Costa Rica"],
        signals=_signals(),
        policy_version="cr-2026.10.1",
        grounded=True,
        validation_notes=[],
    )
    with pytest.raises(ValidationError):
        decision.decision = "unknown"  # type: ignore[misc]


def test_extra_fields_are_rejected_recursively() -> None:
    payload = {
        "decision": "eligible",
        "reason": "The role explicitly permits work from Costa Rica.",
        "evidence": ["Remote in Costa Rica"],
        "signals": {**_signals().model_dump(), "unexpected": True},
        "unexpected": True,
    }
    with pytest.raises(ValidationError) as exc_info:
        EligibilityResponse.model_validate(payload)
    assert len(exc_info.value.errors()) == 2


def test_claimed_signal_requires_its_own_evidence() -> None:
    with pytest.raises(ValidationError, match="locations_named requires evidence"):
        LocationSignals(
            locations_named=["Costa Rica"],
            locations_named_evidence=[],
            remote_scope="none_stated",
            remote_scope_evidence=[],
            exhaustive_country_list=None,
            exhaustive_country_list_evidence=[],
            work_authorization_required_in=[],
            work_authorization_evidence=[],
        )


def test_decision_evidence_has_one_to_five_nonempty_quotes() -> None:
    for evidence in ([], [""]):
        with pytest.raises(ValidationError):
            EligibilityResponse(
                decision="unknown",
                reason="No location scope is stated.",
                evidence=evidence,
                signals=_signals(),
            )


def test_reason_is_one_terminated_line() -> None:
    for reason in ("No terminator", "First sentence.\nSecond sentence."):
        with pytest.raises(ValidationError):
            EligibilityResponse(
                decision="unknown",
                reason=reason,
                evidence=["Remote"],
                signals=_signals(),
            )


def test_json_schema_matches_golden() -> None:
    actual = EligibilityResponse.model_json_schema()
    expected = json.loads(SCHEMA_GOLDEN.read_text(encoding="utf-8"))
    assert actual == expected
