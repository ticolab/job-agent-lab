"""Structured extraction-result models produced by the agent run.

``ExtractionResult`` is the pydantic model the browser-use Agent's
structured output is parsed into. It intentionally lives in ``domain/``
because it is a plain value object with no dependency on browser-use,
LiteLLM, or the CLI — reporting and any future exporter consume it.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ExtractionResult(BaseModel):
    """Structured output model for the browser-use agent."""

    jobs: list[str] = Field(description="List of all job posting URLs found")
