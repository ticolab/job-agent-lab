"""Schema.org JobPosting extraction from JSON-LD script blocks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from typing import Any

from job_pipeline.content.text import main_text, normalize


class _JsonLdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._capture = False
        self._parts: list[str] = []
        self.blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "script":
            return
        attributes = {name.casefold(): value for name, value in attrs}
        if (attributes.get("type") or "").split(";", 1)[0].strip().casefold() == (
            "application/ld+json"
        ):
            self._capture = True
            self._parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._capture:
            self.blocks.append("".join(self._parts))
            self._capture = False
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._parts.append(data)


@dataclass(frozen=True, slots=True)
class JobPostingLd:
    title: str | None
    description_text: str
    identifier: str | None
    date_posted: str | None
    valid_through: str | None
    locations: tuple[str, ...]
    employment_type: tuple[str, ...]
    hiring_org: str | None
    raw: dict[str, Any]

    def is_closed(self, *, today: date | None = None) -> bool:
        """Return whether validThrough is parseable and lies in the past."""
        if not self.valid_through:
            return False
        parsed = _parse_date(self.valid_through)
        return parsed is not None and parsed < (today or datetime.now(UTC).date())


def _parse_date(value: str) -> date | None:
    candidate = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(candidate).date()
    except ValueError:
        try:
            return date.fromisoformat(candidate[:10])
        except ValueError:
            return None


def _objects(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for child in value for item in _objects(child)]
    if not isinstance(value, dict):
        return []
    values = [value]
    graph = value.get("@graph")
    if isinstance(graph, (dict, list)):
        values.extend(_objects(graph))
    return values


def _is_job_posting(value: dict[str, Any]) -> bool:
    kind = value.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return any(
        isinstance(item, str) and item.casefold() == "jobposting" for item in kinds
    )


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        result = normalize(value)
        return result or None
    return None


def _identifier(value: Any) -> str | None:
    """Return the requisition value, never the ``PropertyValue`` name.

    In schema.org's ``PropertyValue`` the ``name`` is conventionally the
    organization and ``value`` the requisition. Falling back to ``name``
    would give every posting on such a board one shared identifier, and the
    duplicate merge would fold the whole company into one job.
    """
    if isinstance(value, dict):
        return _text(value.get("value"))
    return _text(value)


def _locations(value: Any) -> tuple[str, ...]:
    items = value if isinstance(value, list) else [value]
    locations: list[str] = []
    for item in items:
        if isinstance(item, str):
            location = normalize(item)
        elif isinstance(item, dict):
            address = item.get("address", item)
            if isinstance(address, dict):
                parts = [
                    _text(address.get(name))
                    for name in (
                        "streetAddress",
                        "addressLocality",
                        "addressRegion",
                        "postalCode",
                        "addressCountry",
                    )
                ]
                location = ", ".join(part for part in parts if part)
            else:
                location = _text(address) or ""
        else:
            location = ""
        if location and location not in locations:
            locations.append(location)
    return tuple(locations)


def _employment_types(value: Any) -> tuple[str, ...]:
    items = value if isinstance(value, list) else [value]
    return tuple(item for item in (_text(item) for item in items) if item)


def _hiring_org(value: Any) -> str | None:
    if isinstance(value, dict):
        return _text(value.get("name"))
    return _text(value)


def extract_job_postings(html: str) -> tuple[JobPostingLd, ...]:
    """Parse every valid JobPosting object while ignoring malformed blocks."""
    parser = _JsonLdParser()
    parser.feed(html)
    postings: list[JobPostingLd] = []
    for block in parser.blocks:
        try:
            payload = json.loads(block)
        except (json.JSONDecodeError, TypeError):
            continue
        for raw in _objects(payload):
            if not _is_job_posting(raw):
                continue
            description = raw.get("description")
            description_text = (
                main_text(
                    f"<html><body>{description}</body></html>",
                    "https://jsonld.invalid/",
                )[0]
                if isinstance(description, str)
                else ""
            )
            postings.append(
                JobPostingLd(
                    title=_text(raw.get("title")),
                    description_text=description_text,
                    identifier=_identifier(raw.get("identifier")),
                    date_posted=_text(raw.get("datePosted")),
                    valid_through=_text(raw.get("validThrough")),
                    locations=_locations(raw.get("jobLocation")),
                    employment_type=_employment_types(raw.get("employmentType")),
                    hiring_org=_hiring_org(raw.get("hiringOrganization")),
                    raw=raw,
                )
            )
    return tuple(postings)
