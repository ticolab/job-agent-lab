"""Target-region tokens and location predicate.

A :class:`TargetRegion` bundles the two surfaces the runtime needs for
region-scoped extraction:

- ``filter_tokens`` — the UI-visible options the agent may click in a
  location/region filter. Rendered into ``GOAL_PROMPT`` (Cases B and C)
  in :mod:`vacantes.extraction.dom.agent.prompt` and into the Case C tool
  description in :mod:`vacantes.extraction.dom.agent.controller`. The first
  half of the tuple is the *preferred* group and the second half the
  *fallback* group: on a board offering both, the agent picks from the
  preferred group first.

- ``matches(location_name)`` — a predicate the API-based adapters
  (:mod:`vacantes.extraction.ats.greenhouse` and future siblings)
  use to keep or drop a job by its structured location field. Backed
  by two pattern buckets, ``ci_patterns`` (case-insensitive) and
  ``cs_patterns`` (case-sensitive), all word-bounded via ``\b`` at
  match time. The case-sensitive bucket carries acronyms that would
  produce false positives when lowered — ``\bCR\b`` case-insensitive
  matches "Cracow, Poland" via the ``Cr`` prefix, ``\bcri\b`` matches
  many unrelated tokens. The ``CRI`` pattern is *predicate-only*:
  Greenhouse's structured location field emits it (ISO 3166-1 alpha-3),
  but it has never been observed as an actual UI filter option, so it
  is deliberately absent from ``filter_tokens``.

``COSTA_RICA_LATAM`` is the canonical instance and the only region
current needs. Additional regions can be added as sibling module
constants when a second target region is on-boarded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TargetRegion:
    """Region tokens for prompt rendering + API-side location matching.

    Attributes:
        name: Human-readable label used in logs and reports.
        intro_label: Prose noun-phrase form of the region, embedded in
            ``GOAL_PROMPT``'s opening sentence
            (e.g. ``"Costa Rica or Latin America"``).
        filter_tokens: UI-visible option strings the agent may click in
            a location/region filter. The first half is the preferred
            group; the second half is the fallback group.
        ci_patterns: Case-insensitive regex fragments (word-bounded at
            match time) used by :meth:`matches`.
        cs_patterns: Case-sensitive regex fragments (word-bounded at
            match time) used by :meth:`matches`. Reserved for acronyms
            whose lowercase form would collide with common words.
    """

    name: str
    intro_label: str
    filter_tokens: tuple[str, ...]
    ci_patterns: tuple[str, ...]
    cs_patterns: tuple[str, ...]

    def matches(self, location_name: str) -> bool:
        """Return ``True`` if ``location_name`` mentions this region.

        Empty or falsy input returns ``False`` (never raises); callers
        reading a structured location field that may be missing on the
        API payload should coerce to ``""`` first rather than pass
        ``None``.
        """
        if not location_name:
            return False
        for frag in self.ci_patterns:
            if re.search(rf"\b{frag}\b", location_name, re.IGNORECASE):
                return True
        for frag in self.cs_patterns:
            if re.search(rf"\b{frag}\b", location_name):
                return True
        return False

    def format_quoted_options(self) -> str:
        """Render ``filter_tokens`` as a quoted oxford-or list.

        For :data:`COSTA_RICA_LATAM`::

            "Costa Rica", "CR", "LATAM", or "Latin America"

        Consumed by ``GOAL_PROMPT``'s Case B and Case C sentences in
        :mod:`vacantes.extraction.dom.agent.prompt`.
        """
        return _oxford_or_join(tuple(f'"{t}"' for t in self.filter_tokens))

    def format_unquoted_options(self) -> str:
        """Render ``filter_tokens`` as an unquoted oxford-or list.

        For :data:`COSTA_RICA_LATAM`::

            Costa Rica, CR, LATAM, or Latin America

        Consumed by the Case C tool description in
        :mod:`vacantes.extraction.dom.agent.controller`.
        """
        return _oxford_or_join(self.filter_tokens)

    def format_preference_pair(self) -> str:
        """Render the preferred-vs-fallback pair as ``preferring`` prose.

        Splits ``filter_tokens`` in half: the first half is the
        preferred group, the second is the fallback. For
        :data:`COSTA_RICA_LATAM`::

            "Costa Rica"/"CR" over "LATAM"/"Latin America"

        Consumed by ``GOAL_PROMPT``'s Case B sentence.
        """
        mid = len(self.filter_tokens) // 2
        preferred = self.filter_tokens[:mid]
        fallback = self.filter_tokens[mid:]
        return (
            "/".join(f'"{t}"' for t in preferred)
            + " over "
            + "/".join(f'"{t}"' for t in fallback)
        )


def _oxford_or_join(items: tuple[str, ...]) -> str:
    """Join ``items`` as an English ``A, B, or C`` list.

    Two-item input uses the un-comma'd ``A or B`` form; three or more
    items use the oxford-or form (``A, B, or C``). Callers currently
    only pass four-item tuples (the ``filter_tokens`` of the canonical
    :data:`COSTA_RICA_LATAM` region), but the helper is written to
    behave correctly for any length so it stays reusable when a second
    region is added.
    """
    n = len(items)
    if n == 0:
        return ""
    if n == 1:
        return items[0]
    if n == 2:
        return f"{items[0]} or {items[1]}"
    return ", ".join(items[:-1]) + ", or " + items[-1]


# Costa Rican provinces admitted to ``ci_patterns`` below. A board may
# name a Costa Rican location without ever writing the country — HPE's
# Phenom payload carries ``"Heredia, Heredia, 400803"`` — and where no
# server-side facet exists (Greenhouse, BambooHR) the string predicate
# is the *only* region signal, so an unnamed country is a silent
# under-count.
#
# Which names are safe is a measured question, not a geographic one.
# Each of these produced **zero** matches against every recorded API
# payload in ``tests/fixtures/api/`` other than genuine Costa Rica
# postings.
#
# ``San José`` is deliberately **absent**, and must stay absent. It is
# Zscaler's California headquarters: admitting it adds 58 phantom
# postings to that one tenant's recorded fixture, plus Speechify's 3 and
# Varicent's 1. No text rule separates ``San Jose, CA`` from a bare
# ``San Jose`` that means Costa Rica, and a "unless a US marker is
# present" guard fails on the bare form that boards actually write.
# ``tests/unit/test_region.py`` pins the Zscaler strings as
# must-not-match so a future edit adding it fails there rather than in
# the corpus. Phenom tenants do not need it — their server facet is
# authoritative (see ``extraction/ats/phenom``).
#
# Also deliberately absent: ``Liberia`` (a Costa Rican canton and a
# country), ``Santa Ana`` (California, El Salvador), ``Limón`` (low
# volume, ambiguous).
_COSTA_RICA_PROVINCES: tuple[str, ...] = (
    r"alajuela",
    r"heredia",
    r"cartago",
    r"guanacaste",
    r"puntarenas",
)

COSTA_RICA_LATAM = TargetRegion(
    name="Costa Rica / LATAM",
    intro_label="Costa Rica or Latin America",
    filter_tokens=("Costa Rica", "CR", "LATAM", "Latin America"),
    ci_patterns=(
        r"costa\s+rica",
        r"latam",
        r"latin\s+america",
        *_COSTA_RICA_PROVINCES,
    ),
    cs_patterns=(r"CR", r"CRI"),
)
