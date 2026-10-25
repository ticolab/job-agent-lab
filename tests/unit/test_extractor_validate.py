from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from job_extractor.schema import ExtractionResponse
from job_extractor.validate import UngroundedEvidenceError, validate

POSTING = """Senior Python Engineer
Location: San José, Costa Rica. This is a remote full-time role.
You will build APIs with Python 3.11 and React.
Requirements
Python is required.
5 years of software engineering experience.
Preferred qualifications
AWS is a plus.
We use GitHub across the company.
The salary is USD 100,000.
Posted October 23, 2026. Requisition ID ENG-42.
"""


def absent() -> dict[str, object]:
    return {"state": "not_stated", "value": None, "evidence": []}


def extracted(value: Any, evidence: str) -> dict[str, object]:
    return {"state": "extracted", "value": value, "evidence": [evidence]}


def payload() -> dict[str, Any]:
    result = {field: absent() for field in ExtractionResponse.model_fields}
    result.update(
        {
            "title": extracted("Senior Python Engineer", "Senior Python Engineer"),
            "location_text": extracted(
                "San José, Costa Rica", "Location: San José, Costa Rica."
            ),
            "location": extracted(
                {"country": "Costa Rica", "city": "San José"},
                "Location: San José, Costa Rica.",
            ),
            "work_mode": extracted("remote", "This is a remote full-time role."),
            "employment_type": extracted(
                "full_time", "This is a remote full-time role."
            ),
            "experience_level": extracted(
                "senior", "5 years of software engineering experience."
            ),
            "job_functions": extracted(
                ["technology_and_engineering"], "Senior Python Engineer"
            ),
            "summary": extracted(
                "The engineer builds APIs. The role uses Python.",
                "You will build APIs with Python 3.11 and React.",
            ),
            "source_language": extracted("en", "Senior Python Engineer"),
            "responsibilities": extracted(
                ["You will build APIs with Python 3.11 and React."],
                "You will build APIs with Python 3.11 and React.",
            ),
            "required_qualifications": extracted(
                [
                    {
                        "text": "Python is required.",
                        "kind": "skill",
                        "evidence": ["Python is required."],
                    }
                ],
                "Python is required.",
            ),
            "preferred_qualifications": extracted(
                [
                    {
                        "text": "AWS is a plus.",
                        "kind": "skill",
                        "evidence": ["AWS is a plus."],
                    }
                ],
                "AWS is a plus.",
            ),
            "benefits": absent(),
            "compensation_text": extracted("USD 100,000", "The salary is USD 100,000."),
            "technologies": extracted(
                [
                    {
                        "mention": "Python 3.11",
                        "status": "required",
                        "evidence": ["Python is required."],
                    },
                    {
                        "mention": "AWS",
                        "status": "preferred",
                        "evidence": ["AWS is a plus."],
                    },
                    {
                        "mention": "GitHub",
                        "status": "required",
                        "evidence": ["We use GitHub across the company."],
                    },
                ],
                "You will build APIs with Python 3.11 and React.",
            ),
            "posting_date": extracted("2026-10-23", "Posted October 23, 2026."),
            "requisition_id": extracted("ENG-42", "Requisition ID ENG-42."),
        }
    )
    return result


def response(overrides: dict[str, Any] | None = None) -> ExtractionResponse:
    data = payload()
    data.update(overrides or {})
    return ExtractionResponse.model_validate(data)


def test_validate_returns_frozen_versioned_normalized_result() -> None:
    result = validate(response(), POSTING)
    assert result.extractor_version == "2026.10.1"
    assert result.schema_version == 1
    assert [item.canonical for item in result.technologies_normalized] == [
        "Python",
        "Amazon Web Services",
        "GitHub",
    ]
    assert result.technologies.value is not None
    assert result.technologies.value[2].status == "mentioned"
    assert any("lowered GitHub" in note for note in result.validation_notes)
    with pytest.raises(ValidationError):
        result.title = result.title


def test_one_altered_quote_fails_only_its_field() -> None:
    data = payload()
    data["benefits"] = extracted(["Free lunch"], "Free lunch every day")
    result = validate(ExtractionResponse.model_validate(data), POSTING)
    assert result.benefits.state == "failed"
    assert result.benefits.value is None
    assert result.benefits.evidence == []
    assert result.title.state == "extracted"
    assert 'benefits: ungrounded evidence "Free lunch every day"' in (
        result.validation_notes
    )


def test_ungrounded_title_rejects_whole_response() -> None:
    with pytest.raises(UngroundedEvidenceError) as error:
        validate(response({"title": extracted("Wrong", "Wrong title")}), POSTING)
    assert error.value.quotes == ["Wrong title"]


def test_more_than_third_of_extracted_facts_rejects_response() -> None:
    data = {field: absent() for field in ExtractionResponse.model_fields}
    data["title"] = extracted("Senior Python Engineer", "Senior Python Engineer")
    data["summary"] = extracted("A factual summary.", "missing one")
    data["benefits"] = extracted(["Benefit"], "missing two")
    with pytest.raises(UngroundedEvidenceError):
        validate(ExtractionResponse.model_validate(data), POSTING)


def test_location_requires_location_text() -> None:
    result = validate(response({"location_text": absent()}), POSTING)
    assert result.location.state == "failed"
    assert result.location.value is None
    assert result.location.evidence == []
    assert any(
        "requires extracted location_text" in note for note in result.validation_notes
    )


def test_location_requires_grounded_location_text() -> None:
    result = validate(
        response({"location_text": extracted("San José", "missing location")}),
        POSTING,
    )
    assert result.location_text.state == "failed"
    assert result.location.state == "failed"
    assert result.location.value is None


def test_duplicate_preferred_qualification_fails_preferred_only() -> None:
    duplicate = extracted(
        [
            {
                "text": "Python is required.",
                "kind": "skill",
                "evidence": ["Python is required."],
            }
        ],
        "Python is required.",
    )
    result = validate(response({"preferred_qualifications": duplicate}), POSTING)
    assert result.required_qualifications.state == "extracted"
    assert result.preferred_qualifications.state == "failed"
    assert result.preferred_qualifications.value is None
    assert result.preferred_qualifications.evidence == []


def test_fabricated_summary_proper_noun_becomes_uncertain() -> None:
    summary = extracted(
        "The engineer builds APIs. The role uses Kubernetes.",
        "You will build APIs with Python 3.11 and React.",
    )
    result = validate(response({"summary": summary}), POSTING)
    assert result.summary.state == "uncertain"
    assert any("Kubernetes" in note for note in result.validation_notes)


def test_required_technology_traces_to_required_qualification() -> None:
    result = validate(response(), POSTING)
    assert result.technologies.value is not None
    assert result.technologies.value[0].status == "required"


def test_required_technology_traces_to_required_section() -> None:
    technologies = extracted(
        [
            {
                "mention": "Python",
                "status": "required",
                "evidence": ["Python is required."],
            }
        ],
        "Python is required.",
    )
    result = validate(
        response(
            {
                "required_qualifications": absent(),
                "technologies": technologies,
            }
        ),
        POSTING,
    )
    assert result.technologies.value is not None
    assert result.technologies.value[0].status == "required"
