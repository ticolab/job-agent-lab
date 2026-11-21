"""Tests for the strict four-field one-shot eligibility schema."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from litellm.utils import type_to_response_format_param
from pydantic import ValidationError

import job_eligibility
from job_eligibility import (
    MAX_EVIDENCE_CHARACTERS,
    MAX_EVIDENCE_ITEMS,
    MAX_REASON_CHARACTERS,
    EligibilityResponse,
    EligibilityVerdict,
)

SCHEMA_GOLDEN = (
    Path(__file__).resolve().parents[1] / "fixtures" / "schemas" / "eligibility.json"
)
_SUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {
        "$defs",
        "$ref",
        "additionalProperties",
        "anyOf",
        "const",
        "description",
        "enum",
        "items",
        "maxItems",
        "maxLength",
        "minItems",
        "minLength",
        "pattern",
        "properties",
        "required",
        "title",
        "type",
    }
)


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "decision": "eligible",
        "reason": "The role is remote with no exclusion.",
        "supporting_evidence": ["Remote"],
        "conflicting_evidence": [],
    }
    payload.update(overrides)
    return payload


def _assert_supported_strict_schema(schema: object) -> None:
    if isinstance(schema, list):
        for value in schema:
            _assert_supported_strict_schema(value)
        return
    if not isinstance(schema, dict):
        return
    assert not ({"oneOf", "discriminator", "default"} & schema.keys())
    assert set(schema) <= _SUPPORTED_SCHEMA_KEYWORDS
    if schema.get("type") == "object":
        properties = schema.get("properties")
        assert isinstance(properties, dict)
        assert schema.get("additionalProperties") is False
        assert set(schema.get("required", [])) == set(properties)
    for key, value in schema.items():
        if key in {"$defs", "properties"}:
            assert isinstance(value, dict)
            for child in value.values():
                _assert_supported_strict_schema(child)
        else:
            _assert_supported_strict_schema(value)


def test_response_has_exactly_four_model_authored_fields_and_matches_golden() -> None:
    schema = EligibilityResponse.model_json_schema()
    assert list(EligibilityResponse.model_fields) == [
        "decision",
        "reason",
        "supporting_evidence",
        "conflicting_evidence",
    ]
    assert schema["properties"]["decision"]["enum"] == [
        "eligible",
        "ineligible",
        "unknown",
    ]
    for forbidden in ("version", "hash", "confidence", "claims", "rule", "facts"):
        assert not any(forbidden in name for name in schema["properties"])
    assert schema == json.loads(SCHEMA_GOLDEN.read_text(encoding="utf-8"))


def test_litellm_strict_conversion_uses_only_supported_keywords() -> None:
    response_format = type_to_response_format_param(EligibilityResponse)
    assert response_format is not None
    assert response_format["type"] == "json_schema"
    contract = response_format["json_schema"]
    assert contract["strict"] is True
    schema = contract["schema"]
    assert schema["type"] == "object"
    assert "anyOf" not in schema
    _assert_supported_strict_schema(schema)


def test_round_trip_and_extra_fields_are_rejected() -> None:
    response = EligibilityResponse.model_validate(_payload())
    assert EligibilityResponse.model_validate_json(response.model_dump_json()) == (
        response
    )
    with pytest.raises(ValidationError):
        EligibilityResponse.model_validate(_payload(rule_id="OSV2-007-REMOTE"))
    with pytest.raises(ValidationError):
        EligibilityResponse.model_validate({"decision": "eligible"})


@pytest.mark.parametrize(
    "field",
    ["supporting_evidence", "conflicting_evidence"],
)
def test_evidence_items_are_nonblank_bounded_excerpts(field: str) -> None:
    EligibilityResponse.model_validate(
        _payload(**{field: ["x" * MAX_EVIDENCE_CHARACTERS]})
    )
    for invalid in ("", "   ", "x" * (MAX_EVIDENCE_CHARACTERS + 1)):
        with pytest.raises(ValidationError):
            EligibilityResponse.model_validate(_payload(**{field: [invalid]}))
    with pytest.raises(ValidationError):
        EligibilityResponse.model_validate(
            _payload(
                **{field: [f"q{index}" for index in range(MAX_EVIDENCE_ITEMS + 1)]}
            )
        )
    # Multi-line excerpts are legal: posting layouts often split a label and value.
    EligibilityResponse.model_validate(_payload(**{field: ["Job Location\nLATAM"]}))


@pytest.mark.parametrize(
    "reason",
    [
        "",
        " Leading space is rejected.",
        "No terminal punctuation",
        "Two lines.\nAre rejected.",
        "Carriage\rreturn.",
        "x" * MAX_REASON_CHARACTERS + ".",
    ],
)
def test_reason_is_one_bounded_terminated_line(reason: str) -> None:
    with pytest.raises(ValidationError):
        EligibilityResponse.model_validate(_payload(reason=reason))


def test_verdict_is_frozen_and_not_exported_as_model_contract() -> None:
    verdict = EligibilityVerdict(
        decision="eligible",
        reason="Remote.",
        supporting_evidence=("Remote",),
        conflicting_evidence=(),
        eligibility_version="v",
        prompt_hash="h",
        grounded=True,
    )
    with pytest.raises(ValidationError):
        verdict.decision = "unknown"  # type: ignore[misc]
    obsolete = {
        "EligibilityFactsResponse",
        "FACTS_VERSION",
        "FACTS_PROMPT_HASH",
        "SCREENING_POLICY_VERSION",
        "SCREENING_RULE_IDS",
        "screen",
        "validate_facts",
        "build_facts_messages",
        "COSTA_RICA_NAMES",
        "LocationSignals",
        "POLICY_VERSION",
    }
    assert obsolete.isdisjoint(job_eligibility.__all__)
    assert all(not hasattr(job_eligibility, name) for name in obsolete)
