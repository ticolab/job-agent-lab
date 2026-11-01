"""Strict structured-output contracts for Costa Rica job eligibility."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Decision = Literal["eligible", "ineligible", "unknown"]
Quote = Annotated[str, Field(min_length=1)]
Evidence = Annotated[list[Quote], Field(max_length=5)]
RequiredEvidence = Annotated[list[Quote], Field(min_length=1, max_length=5)]
RemoteScope = Literal[
    "worldwide",
    "latam",
    "americas",
    "other_region",
    "none_stated",
]


class LocationSignals(BaseModel):
    """Location facts extracted from a posting with field-specific evidence."""

    model_config = ConfigDict(extra="forbid")

    locations_named: list[str]
    locations_named_evidence: Evidence
    remote_scope: RemoteScope
    remote_scope_evidence: Evidence
    exhaustive_country_list: list[str] | None
    exhaustive_country_list_evidence: Evidence
    work_authorization_required_in: list[str]
    work_authorization_evidence: Evidence

    @field_validator("locations_named", "work_authorization_required_in")
    @classmethod
    def require_nonempty_values(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("signal values must not be empty")
        return values

    @field_validator("exhaustive_country_list")
    @classmethod
    def require_nonempty_countries(cls, values: list[str] | None) -> list[str] | None:
        if values is not None and any(not value.strip() for value in values):
            raise ValueError("country names must not be empty")
        return values

    @model_validator(mode="after")
    def require_evidence_for_claims(self) -> LocationSignals:
        pairs = (
            (self.locations_named, self.locations_named_evidence, "locations_named"),
            (
                self.exhaustive_country_list,
                self.exhaustive_country_list_evidence,
                "exhaustive_country_list",
            ),
            (
                self.work_authorization_required_in,
                self.work_authorization_evidence,
                "work_authorization_required_in",
            ),
        )
        for value, evidence, name in pairs:
            if value and not evidence:
                raise ValueError(f"{name} requires evidence")
        if self.remote_scope != "none_stated" and not self.remote_scope_evidence:
            raise ValueError("remote_scope requires evidence when a scope is stated")
        return self


class EligibilityResponse(BaseModel):
    """The model response before deterministic grounding and policy checks."""

    model_config = ConfigDict(extra="forbid")

    decision: Decision
    reason: str = Field(min_length=1, pattern=r"^[^\n]+[.!?]$")
    evidence: RequiredEvidence
    signals: LocationSignals


class EligibilityDecision(EligibilityResponse):
    """A grounded response after deterministic policy consistency checks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str
    grounded: bool
    validation_notes: list[str]
