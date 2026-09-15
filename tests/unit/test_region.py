"""Unit tests for :class:`vacantes.domain.region.TargetRegion`.

Locks in the two surfaces of the canonical :data:`COSTA_RICA_LATAM`
instance:

- ``matches(location_name)`` — the API-side predicate that
  Greenhouse-style adapters will use. The positives are the concrete
  location strings recorded from Zscaler, Movable Ink, and West Monroe
  Greenhouse boards in ``spike/INTEGRATION_BLOCKERS.md`` (plus generic
  variants); the negatives target the case-sensitivity trap (``CR``
  must not match ``Cracow``, ``CRM``) and the ``latin\\s+america`` word
  boundary (``Latina, Italy`` must not match).

- The three ``format_*`` render helpers — quoted / unquoted / preference
  pair — used by :func:`vacantes.extraction.dom.agent.prompt.build_goal_prompt`
  and :func:`vacantes.extraction.dom.agent.controller.build_no_match_description`.
  Goldens on the composed strings live in ``test_prompt_render.py``;
  these tests pin the render primitives directly.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from vacantes.domain.region import COSTA_RICA_LATAM


class TestMatchesPositives:
    """Locations that must match the ``COSTA_RICA_LATAM`` predicate."""

    @pytest.mark.parametrize(
        "location",
        [
            # Recorded on Zscaler (case-sensitive `CRI`, ISO-3 alpha).
            "Escazu, CRI",
            # Recorded on Movable Ink.
            "Movable Ink - Costa Rica",
            # Recorded on West Monroe (bare region name).
            "Costa Rica",
            # Common Greenhouse compound-location shapes.
            "Remote - Costa Rica",
            "Heredia, Costa Rica",
            # Case-sensitive `CR` with a non-word neighbour.
            "CR-San Jose",
        ],
    )
    def test_recorded_location_matches(self, location: str) -> None:
        assert COSTA_RICA_LATAM.matches(location)

    def test_lowercase_costa_rica_matches(self) -> None:
        # `costa\s+rica` is in the case-insensitive bucket.
        assert COSTA_RICA_LATAM.matches("costa rica")

    def test_multi_space_costa_rica_matches(self) -> None:
        # `\s+` in the pattern permits arbitrary whitespace between
        # ``Costa`` and ``Rica`` (Greenhouse sometimes pads).
        assert COSTA_RICA_LATAM.matches("Costa  Rica")

    def test_latam_matches(self) -> None:
        assert COSTA_RICA_LATAM.matches("LATAM Team")

    def test_latin_america_matches(self) -> None:
        assert COSTA_RICA_LATAM.matches("Remote - Latin America")


class TestMatchesNegatives:
    """Locations that must NOT match — guarding against false positives."""

    @pytest.mark.parametrize(
        "location",
        [
            # Substring-of-``Cracow`` starts with ``Cr`` — case-sensitive
            # ``\bCR\b`` must not match.
            "Cracow, Poland",
            # Only ``San Jose``; the CR component is absent.
            "San Jose, California",
            # ``CRM`` starts with ``CR`` but has no word boundary between
            # ``R`` and ``M``; the case-sensitive ``\bCR\b`` must reject it.
            "CRM Specialist - Austin",
            # ``Latina`` is a word on its own, not part of ``Latin
            # America``; the ``\blatin\s+america\b`` predicate must
            # not match.
            "Latina, Italy",
            # Empty input is a non-match, not an error.
            "",
        ],
    )
    def test_recorded_negative_does_not_match(self, location: str) -> None:
        assert not COSTA_RICA_LATAM.matches(location)


class TestMatchesCaseSensitivity:
    """The ``cs_patterns`` bucket must genuinely be case-sensitive."""

    def test_lowercase_cr_does_not_match(self) -> None:
        # A bare ``cr`` must not match; the acronym predicate lives in
        # the case-sensitive bucket precisely to avoid this false
        # positive.
        assert not COSTA_RICA_LATAM.matches("cr")

    def test_lowercase_cri_does_not_match(self) -> None:
        assert not COSTA_RICA_LATAM.matches("cri")

    def test_lowercase_latam_still_matches(self) -> None:
        # ``latam`` is in the case-insensitive bucket, so lower-case
        # remains a match. Guards against accidentally moving it to the
        # case-sensitive bucket in a future refactor.
        assert COSTA_RICA_LATAM.matches("latam remote")


class TestFilterTokenRendering:
    """The three render helpers that feed the prompt + tool description."""

    def test_format_quoted_options(self) -> None:
        assert COSTA_RICA_LATAM.format_quoted_options() == (
            '"Costa Rica", "CR", "LATAM", or "Latin America"'
        )

    def test_format_unquoted_options(self) -> None:
        assert COSTA_RICA_LATAM.format_unquoted_options() == (
            "Costa Rica, CR, LATAM, or Latin America"
        )

    def test_format_preference_pair(self) -> None:
        assert COSTA_RICA_LATAM.format_preference_pair() == (
            '"Costa Rica"/"CR" over "LATAM"/"Latin America"'
        )


class TestFrozen:
    """``TargetRegion`` is a frozen dataclass — mutation must raise."""

    def test_cannot_reassign_field(self) -> None:
        with pytest.raises(FrozenInstanceError):
            COSTA_RICA_LATAM.name = "Other"  # type: ignore[misc]
