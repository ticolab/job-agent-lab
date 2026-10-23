from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

SCHEMA_VERSION = 1

ModelFieldState = Literal["extracted", "not_stated", "uncertain"]
FieldState = Literal["extracted", "not_stated", "uncertain", "failed"]
TechnologyStatus = Literal["required", "preferred", "mentioned"]
QualificationKind = Literal[
    "skill",
    "experience",
    "education",
    "certification",
    "language",
    "behavioral",
    "other",
]


class WorkMode(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"


class EmploymentType(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    FREELANCE = "freelance"
    TEMPORARY = "temporary"
    INTERNSHIP = "internship"


class ExperienceLevel(StrEnum):
    ENTRY = "entry"
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    LEAD = "lead"
    PRINCIPAL = "principal"
    EXECUTIVE = "executive"


class JobFunction(StrEnum):
    TECHNOLOGY_AND_ENGINEERING = "technology_and_engineering"
    SALES_AND_BUSINESS_DEVELOPMENT = "sales_and_business_development"
    MARKETING_AND_COMMUNICATIONS = "marketing_and_communications"
    OPERATIONS_AND_LOGISTICS = "operations_and_logistics"
    FINANCE_AND_ACCOUNTING = "finance_and_accounting"
    HUMAN_RESOURCES = "human_resources"
    CUSTOMER_SUCCESS_AND_SUPPORT = "customer_success_and_support"
    PRODUCT_MANAGEMENT = "product_management"
    DATA_AND_ANALYTICS = "data_and_analytics"
    HEALTHCARE_AND_MEDICAL = "healthcare_and_medical"
    LEGAL_AND_COMPLIANCE = "legal_and_compliance"
    DESIGN_AND_CREATIVE = "design_and_creative"
    ADMINISTRATIVE_AND_OFFICE = "administrative_and_office"
    CONSULTING_AND_STRATEGY = "consulting_and_strategy"
    GENERAL_MANAGEMENT = "general_management"
    OTHER = "other"


class _FactRules[T](BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: T | None
    evidence: list[str]

    @field_validator("evidence")
    @classmethod
    def evidence_must_be_quotes(cls, evidence: list[str]) -> list[str]:
        if any(not quote.strip() for quote in evidence):
            raise ValueError("evidence quotes must not be empty")
        return evidence

    def _validate_state(self, state: FieldState) -> None:
        if state == "extracted" and (self.value is None or not self.evidence):
            raise ValueError("extracted facts require a value and evidence")
        if state == "not_stated" and (self.value is not None or self.evidence):
            raise ValueError("not_stated facts require no value and no evidence")
        if state == "uncertain" and not self.evidence:
            raise ValueError("uncertain facts require evidence")
        if state == "failed" and (self.value is not None or self.evidence):
            raise ValueError("failed facts require no value and no evidence")


class ModelFact[T](_FactRules[T]):
    state: ModelFieldState

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        self._validate_state(self.state)
        return self


class Fact[T](_FactRules[T]):
    state: FieldState

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        self._validate_state(self.state)
        return self


class Location(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    country: str
    region: str | None = None
    city: str | None = None

    @field_validator("country")
    @classmethod
    def country_must_not_be_blank(cls, country: str) -> str:
        if not country.strip():
            raise ValueError("location country must not be blank")
        return country


class Qualification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    kind: QualificationKind
    evidence: list[str]

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, text: str) -> str:
        if not text.strip():
            raise ValueError("qualification text must not be blank")
        return text

    @field_validator("evidence")
    @classmethod
    def evidence_must_contain_quotes(cls, evidence: list[str]) -> list[str]:
        if not evidence or any(not quote.strip() for quote in evidence):
            raise ValueError("qualification evidence must contain nonempty quotes")
        return evidence


class TechnologyMention(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mention: str
    status: TechnologyStatus
    evidence: list[str]

    @field_validator("mention")
    @classmethod
    def mention_must_not_be_blank(cls, mention: str) -> str:
        if not mention.strip():
            raise ValueError("technology mention must not be blank")
        return mention

    @field_validator("evidence")
    @classmethod
    def evidence_must_contain_quotes(cls, evidence: list[str]) -> list[str]:
        if not evidence or any(not quote.strip() for quote in evidence):
            raise ValueError("technology evidence must contain nonempty quotes")
        return evidence


class Technology(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical: str | None
    kind: str | None
    status: TechnologyStatus
    mentions: list[TechnologyMention]


class ExtractionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: ModelFact[str]
    location_text: ModelFact[str]
    location: ModelFact[Location]
    work_mode: ModelFact[WorkMode]
    employment_type: ModelFact[EmploymentType]
    experience_level: ModelFact[ExperienceLevel]
    job_functions: ModelFact[list[JobFunction]]
    summary: ModelFact[str]
    source_language: ModelFact[str]
    responsibilities: ModelFact[list[str]]
    required_qualifications: ModelFact[list[Qualification]]
    preferred_qualifications: ModelFact[list[Qualification]]
    benefits: ModelFact[list[str]]
    compensation_text: ModelFact[str]
    technologies: ModelFact[list[TechnologyMention]]
    posting_date: ModelFact[date]
    requisition_id: ModelFact[str]

    @field_validator("source_language")
    @classmethod
    def source_language_must_be_iso_639_1(cls, fact: ModelFact[str]) -> ModelFact[str]:
        if fact.value is not None and (
            len(fact.value) != 2 or not fact.value.isascii() or not fact.value.islower()
        ):
            raise ValueError("source language must be a lowercase ISO 639-1 code")
        return fact


class JobResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: Fact[str]
    location_text: Fact[str]
    location: Fact[Location]
    work_mode: Fact[WorkMode]
    employment_type: Fact[EmploymentType]
    experience_level: Fact[ExperienceLevel]
    job_functions: Fact[list[JobFunction]]
    summary: Fact[str]
    source_language: Fact[str]
    responsibilities: Fact[list[str]]
    required_qualifications: Fact[list[Qualification]]
    preferred_qualifications: Fact[list[Qualification]]
    benefits: Fact[list[str]]
    compensation_text: Fact[str]
    technologies: Fact[list[TechnologyMention]]
    posting_date: Fact[date]
    requisition_id: Fact[str]
    technologies_normalized: list[Technology]
    extractor_version: str
    schema_version: int
    validation_notes: list[str]
