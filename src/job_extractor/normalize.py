from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib.resources import files
from typing import cast

from job_extractor.schema import Technology, TechnologyMention, TechnologyStatus

_VERSION_SUFFIX = re.compile(r"(?i)(?:\s+v?\d+(?:\.\d+)*(?:\+)?|\s+\d+\+)$")
_STATUS_RANK: dict[TechnologyStatus, int] = {
    "mentioned": 0,
    "preferred": 1,
    "required": 2,
}


@dataclass(frozen=True)
class TechnologyEntry:
    canonical: str
    aliases: tuple[str, ...]
    kind: str


def load_technology_entries() -> tuple[TechnologyEntry, ...]:
    resource = files("job_extractor.data").joinpath("technologies.json")
    raw = cast(
        list[dict[str, object]], json.loads(resource.read_text(encoding="utf-8"))
    )
    return tuple(
        TechnologyEntry(
            canonical=cast(str, item["canonical"]),
            aliases=tuple(cast(list[str], item["aliases"])),
            kind=cast(str, item["kind"]),
        )
        for item in raw
    )


def _lookup_key(mention: str) -> str:
    return _VERSION_SUFFIX.sub("", mention.strip()).casefold()


def normalize(mentions: list[TechnologyMention]) -> list[Technology]:
    entries = load_technology_entries()
    aliases = {alias.casefold(): entry for entry in entries for alias in entry.aliases}
    grouped: dict[str, tuple[TechnologyEntry | None, list[TechnologyMention]]] = {}

    for mention in mentions:
        entry = aliases.get(_lookup_key(mention.mention))
        key = (
            entry.canonical
            if entry is not None
            else f"raw:{mention.mention.casefold()}"
        )
        if key not in grouped:
            grouped[key] = (entry, [])
        grouped[key][1].append(mention)

    technologies: list[Technology] = []
    for entry, grouped_mentions in grouped.values():
        status = max(
            grouped_mentions, key=lambda item: _STATUS_RANK[item.status]
        ).status
        technologies.append(
            Technology(
                canonical=entry.canonical if entry is not None else None,
                kind=entry.kind if entry is not None else None,
                status=status,
                mentions=grouped_mentions,
            )
        )
    return technologies
