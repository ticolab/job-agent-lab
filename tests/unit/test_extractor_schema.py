from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from job_extractor.schema import (
    SCHEMA_VERSION,
    ExtractionResponse,
    Fact,
    JobFunction,
    Location,
    ModelFact,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "job_extractor"


def absent() -> dict[str, object]:
    return {"state": "not_stated", "value": None, "evidence": []}


def response_payload() -> dict[str, Any]:
    payload = {field: absent() for field in ExtractionResponse.model_fields}
    payload.update(
        {
            "title": {
                "state": "extracted",
                "value": "Senior Engineer",
                "evidence": ["Senior Engineer"],
            },
            "source_language": {
                "state": "extracted",
                "value": "en",
                "evidence": ["Senior Engineer"],
            },
            "posting_date": {
                "state": "extracted",
                "value": "2026-10-23",
                "evidence": ["Posted October 23, 2026"],
            },
        }
    )
    return payload


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"state": "extracted", "value": None, "evidence": ["x"]}, "value"),
        ({"state": "extracted", "value": "x", "evidence": []}, "evidence"),
        ({"state": "not_stated", "value": "x", "evidence": []}, "no value"),
        ({"state": "not_stated", "value": None, "evidence": ["x"]}, "no value"),
        ({"state": "uncertain", "value": None, "evidence": []}, "evidence"),
    ],
)
def test_fact_state_rules(payload: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Fact[str].model_validate(payload)


def test_fact_accepts_all_valid_states() -> None:
    assert Fact[str](state="extracted", value="x", evidence=["x"]).value == "x"
    assert Fact[str](state="not_stated", value=None, evidence=[]).value is None
    assert Fact[str](state="uncertain", value=None, evidence=["maybe"]).value is None
    assert Fact[str](state="failed", value=None, evidence=[]).state == "failed"
    with pytest.raises(ValidationError, match="no value"):
        Fact[str](state="failed", value="x", evidence=["x"])


def test_model_fact_cannot_return_failed() -> None:
    with pytest.raises(ValidationError):
        ModelFact[str].model_validate(
            {"state": "failed", "value": "x", "evidence": ["x"]}
        )


def test_location_requires_nonblank_country() -> None:
    with pytest.raises(ValidationError, match="country"):
        Location(country="")


def test_response_round_trip_and_types() -> None:
    response = ExtractionResponse.model_validate(response_payload())
    round_tripped = ExtractionResponse.model_validate_json(response.model_dump_json())
    assert round_tripped == response
    assert round_tripped.posting_date.value == date(2026, 10, 23)
    assert SCHEMA_VERSION == 1


@pytest.mark.parametrize("language", ["eng", "EN", "éñ"])
def test_source_language_must_be_iso_639_1_shape(language: str) -> None:
    payload = response_payload()
    payload["source_language"] = {
        "state": "extracted",
        "value": language,
        "evidence": ["English"],
    }
    with pytest.raises(ValidationError, match="ISO 639-1"):
        ExtractionResponse.model_validate(payload)


def test_job_functions_are_the_legacy_sixteen() -> None:
    assert len(JobFunction) == 16
    assert JobFunction.DATA_AND_ANALYTICS.value == "data_and_analytics"
    assert JobFunction.TECHNOLOGY_AND_ENGINEERING.value == (
        "technology_and_engineering"
    )


def test_schema_matches_golden() -> None:
    golden = json.loads((FIXTURES / "extraction_response.schema.json").read_text())
    assert ExtractionResponse.model_json_schema() == golden
