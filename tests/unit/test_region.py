"""Unit tests for :class:`vacantes.domain.region.TargetRegion`.

Locks in the two surfaces of the canonical :data:`COSTA_RICA_LATAM`
instance:

- ``matches(location_name)`` — the API-side predicate that
  Greenhouse-style adapters will use. The positives are the concrete
  location strings recorded from Zscaler, Movable Ink, and West Monroe
  Greenhouse boards in ``blockers/INTEGRATION_BLOCKERS.md`` (plus generic
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

import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import ClassVar

import pytest

from vacantes.domain.region import (
    _COSTA_RICA_PROVINCES,
    COSTA_RICA_LATAM,
    TargetRegion,
)


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


class TestCostaRicanProvinces:
    """Province names admitted so an unnamed country is not a blind spot.

    A board may write a Costa Rican location without ever writing the
    country — HPE's Phenom payload carries ``"Heredia, Heredia,
    400803"``. Where a server-side region facet exists that is merely
    untidy, but Greenhouse and BambooHR have no facet at all, so this
    predicate is their only region signal and an unnamed country is a
    silent under-count.

    Admission was decided by measurement, not geography: each name below
    produced zero matches against every recorded payload in
    ``tests/fixtures/api/`` other than genuine Costa Rica postings.
    """

    @pytest.mark.parametrize(
        "location",
        [
            "Heredia, Heredia, 400803",
            "Heredia",
            "Alajuela, Alajuela",
            "Cartago, Cartago, 30101",
            "Guanacaste, Liberia",
            "Puntarenas",
            "HEREDIA",
            "heredia, costa rica",
        ],
    )
    def test_province_names_match(self, location: str) -> None:
        assert COSTA_RICA_LATAM.matches(location) is True

    @pytest.mark.parametrize(
        "location",
        [
            # Substrings must not match — the patterns are word-bounded.
            "Heredian Consulting Group",
            "Cartagena, Colombia",
            "Alajuelita Software",
        ],
    )
    def test_word_boundaries_hold(self, location: str) -> None:
        assert COSTA_RICA_LATAM.matches(location) is False


class TestSanJoseIsExcluded:
    """``San José`` must never be admitted to the region patterns.

    This class exists to fail loudly if someone adds it. It is the most
    natural-looking addition — San José is Costa Rica's capital and the
    province where most postings sit — and it is the one that breaks the
    corpus, because it is also Zscaler's California headquarters.

    Measured on the recorded fixtures at the time of writing: admitting
    ``san\\s+jos[eé]`` adds **58** phantom postings to
    ``tests/fixtures/api/greenhouse/zscaler.json`` (which holds 2 genuine
    Costa Rica jobs), 3 to Speechify and 1 to Varicent. No text rule
    separates ``San Jose, CA`` from a bare ``San Jose`` meaning Costa
    Rica, and a "unless a US marker is present" guard fails on exactly
    the bare form boards write.

    Phenom tenants need no such rule: their server facet is
    authoritative (see ``extraction/ats/phenom``), which is how HPE's
    ``"San Jose, San Jose, 00000"`` records are kept without the
    predicate ever having to place them.
    """

    @pytest.mark.parametrize(
        "location",
        [
            "San Jose, CA, USA",
            "San Jose, California",
            "San Jose, California, USA",
            "San Jose",
            "San José",
            "San Jose, San Jose, 00000",
        ],
    )
    def test_san_jose_never_matches(self, location: str) -> None:
        assert COSTA_RICA_LATAM.matches(location) is False, (
            "San José must stay out of the region patterns; see this class's "
            "docstring for the Zscaler measurement."
        )

    def test_a_costa_rican_san_jose_still_matches_via_its_country(self) -> None:
        # The exclusion costs nothing when the country *is* named, which
        # is how every corpus board other than HPE writes it.
        assert COSTA_RICA_LATAM.matches("San José, Costa Rica") is True
        assert COSTA_RICA_LATAM.matches("Sabana Norte, San Jose, Costa Rica") is True


class TestRecordedFixturesAreUnaffected:
    """Widening ``ci_patterns`` must change nothing in the recorded corpus.

    A guard on the *act of widening*, not on any one name. It rebuilds
    the predicate without the province patterns and asserts the two
    agree on every location string the repo has frozen — so a future
    addition is measured against real payloads rather than argued about.

    Greenhouse and BambooHR matter most: they expose no server-side
    region facet, so this predicate is their only signal and a false
    positive lands straight in the corpus. ``San José`` is the name that
    fails this test loudly (58 Zscaler postings); the five provinces
    currently admitted pass it with a delta of zero.

    A delta is not automatically a bug — a new province might legitimately
    match a board this corpus has not recorded yet — but it must be an
    explicit, reviewed change, which is what failing here forces.
    """

    @staticmethod
    def _without_provinces() -> TargetRegion:
        base = COSTA_RICA_LATAM
        keep = tuple(
            pat for pat in base.ci_patterns if pat not in _COSTA_RICA_PROVINCES
        )
        return TargetRegion(
            name=base.name,
            intro_label=base.intro_label,
            filter_tokens=base.filter_tokens,
            ci_patterns=keep,
            cs_patterns=base.cs_patterns,
        )

    def _location_strings(self) -> list[tuple[str, str]]:
        fixtures = Path(__file__).resolve().parent.parent / "fixtures" / "api"
        out: list[tuple[str, str]] = []
        for path in sorted((fixtures / "greenhouse").glob("*.json")):
            for job in json.loads(path.read_text(encoding="utf-8")).get("jobs", []):
                name = (job.get("location") or {}).get("name", "") or ""
                if name:
                    out.append((path.stem, name))
        for path in sorted((fixtures / "phenom").glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            for job in payload.get("refineSearch", {}).get("data", {}).get("jobs", []):
                primary = f"{job.get('country', '')} {job.get('city', '')}".strip()
                if primary:
                    out.append((path.stem, primary))
                for entry in job.get("multi_location_array") or []:
                    loc = entry.get("location") if isinstance(entry, dict) else None
                    if isinstance(loc, str) and loc:
                        out.append((path.stem, loc))
        return out

    # Every location string the province patterns newly match across the
    # recorded corpus, reviewed one by one. Each must be a genuine Costa
    # Rica location that no other pattern could reach — which is the
    # whole point of admitting the provinces. HPE writes Heredia with a
    # postcode and no country, so ``costa rica`` / ``CRI`` never fire on
    # it; before this widening it matched nothing.
    _REVIEWED_NEW_MATCHES: ClassVar[set[tuple[str, str]]] = {
        ("hpe", "Heredia, Heredia, 400803"),
    }

    def test_province_matches_are_exactly_the_reviewed_set(self) -> None:
        narrow = self._without_provinces()
        strings = self._location_strings()
        assert len(strings) > 500, "fixture corpus unexpectedly small"
        newly = {
            (fixture, loc)
            for fixture, loc in strings
            if COSTA_RICA_LATAM.matches(loc) and not narrow.matches(loc)
        }
        assert newly == self._REVIEWED_NEW_MATCHES, (
            "the province patterns' effect on the recorded corpus changed. "
            f"unexpected={sorted(newly - self._REVIEWED_NEW_MATCHES)}, "
            f"gone={sorted(self._REVIEWED_NEW_MATCHES - newly)}. Each new "
            "entry must be a genuine Costa Rica location before it is added "
            "here — a US city reached by a province name is the failure this "
            "guard exists to catch."
        )

    def test_the_guard_itself_works(self) -> None:
        # Negative control: San José would be caught. Without this, a
        # broken comparison above would pass silently and the guard would
        # be decorative.
        with_san_jose = TargetRegion(
            name=COSTA_RICA_LATAM.name,
            intro_label=COSTA_RICA_LATAM.intro_label,
            filter_tokens=COSTA_RICA_LATAM.filter_tokens,
            ci_patterns=(*COSTA_RICA_LATAM.ci_patterns, r"san\s+jos[e\u00e9]"),
            cs_patterns=COSTA_RICA_LATAM.cs_patterns,
        )
        newly = [
            loc
            for _, loc in self._location_strings()
            if with_san_jose.matches(loc) and not COSTA_RICA_LATAM.matches(loc)
        ]
        assert len(newly) >= 50, (
            "expected San José to introduce a large number of California "
            f"false positives; got {len(newly)}"
        )
