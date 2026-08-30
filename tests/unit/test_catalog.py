"""Unit tests for ``job_agent_lab.catalog``.

Locks the handle-resolution contract that the CLI, the capture script,
and the ``integrate-company`` skill all rely on. The contract has two
levels:

* Across companies: iteration follows ``COMPANIES`` list order; the
  first entry matching *any* of the three rules wins.
* Within a single company: rules are checked in alias > acronym >
  substring order (case-insensitive), but this precedence is not
  externally observable because only one ``Company`` object is returned.

Also locks the ``slugify`` output shape that feeds ``output/`` filenames
and snapshot directory names. None of these tests touch the browser,
the LLM, or the filesystem.
"""

from __future__ import annotations

import pytest

from job_agent_lab.catalog import _acronym, find_company, slugify
from job_agent_lab.domain.company import Company, LinkRule


class TestSlugify:
    """Slug rules: lowercase, non-alphanumerics collapse to '_', strip ends."""

    def test_simple_name(self) -> None:
        assert slugify("Akurey") == "akurey"

    def test_multi_word_name_gets_underscored(self) -> None:
        assert slugify("Growth Acceleration Partners") == "growth_acceleration_partners"

    def test_punctuation_and_ampersand_collapse(self) -> None:
        # Snapshot directory names must be filesystem-safe on macOS and
        # Linux: no punctuation, no ampersand, no spaces.
        assert slugify("Foo & Bar, Inc.") == "foo_bar_inc"

    def test_leading_and_trailing_non_alnum_stripped(self) -> None:
        assert slugify("  --Zencore--  ") == "zencore"

    def test_unicode_letters_are_dropped(self) -> None:
        # The regex is strictly [a-z0-9], so non-ASCII letters collapse
        # to underscore. This is intentional — snapshot directories are
        # ASCII-only.
        assert slugify("Café Móvil") == "caf_m_vil"


class TestAcronym:
    """Auto-derived lowercase acronyms from company names."""

    def test_multi_word_acronym(self) -> None:
        assert _acronym("Growth Acceleration Partners") == "gap"

    def test_single_word_becomes_single_letter(self) -> None:
        assert _acronym("Akurey") == "a"

    def test_empty_words_ignored(self) -> None:
        # Extra whitespace between words does not produce empty-string
        # letters.
        assert _acronym("  Sumo   Logic  ") == "sl"


def _company(name: str, *, aliases: tuple[str, ...] = ()) -> Company:
    """Build a minimal test Company; job_board_url and sample_job_url are
    filler because handle resolution doesn't touch them."""
    return Company(
        name=name,
        aliases=aliases,
        job_board_url="https://example.com/careers",
        sample_job_url="https://example.com/jobs/1",
        link_rule=LinkRule(),
    )


class TestFindCompany:
    """Observable contract of ``find_company``.

    The implementation iterates ``COMPANIES`` in list order and, for each
    entry, checks alias > acronym > substring, returning the first company
    that matches *any* of the three rules. This means:

    * Insertion order is authoritative across companies — if two entries
      both match, the earlier one wins regardless of which rule triggered.
    * Rule precedence (alias > acronym > substring) only matters for
      deciding the return path *within* a single company, which is not
      externally observable (only the ``Company`` object is returned).

    These tests use ``monkeypatch`` to swap in a small deterministic
    COMPANIES list so each test isolates one behaviour.
    """

    def test_alias_resolves(self, monkeypatch: pytest.MonkeyPatch) -> None:
        gap = _company("Growth Acceleration Partners", aliases=("gap",))
        monkeypatch.setattr("job_agent_lab.catalog.COMPANIES", [gap])
        assert find_company("gap") is gap

    def test_acronym_resolves(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sumo = _company("Sumo Logic")
        monkeypatch.setattr("job_agent_lab.catalog.COMPANIES", [sumo])
        assert find_company("sl") is sumo

    def test_substring_resolves(self, monkeypatch: pytest.MonkeyPatch) -> None:
        akamai = _company("Akamai")
        monkeypatch.setattr("job_agent_lab.catalog.COMPANIES", [akamai])
        assert find_company("kama") is akamai

    def test_insertion_order_wins_across_companies(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Both "Slack" (substring) and "Sumo Logic" (acronym "sl") match
        # the query "sl". The first entry in COMPANIES wins regardless of
        # which rule triggered — rule precedence is intra-company only.
        slack = _company("Slack")
        sumo = _company("Sumo Logic")
        monkeypatch.setattr("job_agent_lab.catalog.COMPANIES", [slack, sumo])
        assert find_company("sl") is slack

    def test_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        gap = _company("Growth Acceleration Partners", aliases=("gap",))
        monkeypatch.setattr("job_agent_lab.catalog.COMPANIES", [gap])
        assert find_company("GAP") is gap
        assert find_company("  gAp  ") is gap

    def test_miss_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        akurey = _company("Akurey")
        monkeypatch.setattr("job_agent_lab.catalog.COMPANIES", [akurey])
        assert find_company("nonexistent") is None

    def test_empty_query_matches_first_company(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Edge case: the empty string is a substring of every name, so it
        # resolves to the first entry. Not a "correct" outcome per se, but
        # locking the behaviour so a future refactor doesn't silently
        # change it.
        first = _company("Akurey")
        second = _company("Zencore")
        monkeypatch.setattr("job_agent_lab.catalog.COMPANIES", [first, second])
        assert find_company("") is first
