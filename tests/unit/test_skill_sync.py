"""Keep the two ``integrate-company`` skill copies from drifting apart.

The skill is maintained twice, once per assistant: ``.claude/skills/`` for
Claude Code and ``.tabnine/agent/skills/`` for the Tabnine CLI. The copies
are meant to be identical except for two deliberate adaptations — the
Playwright MCP tool prefix each assistant exposes, and the guidance file
each cites for the commit convention. Until this test existed that rule
was kept by review alone, and an edit landing in one copy only would leave
the other assistant running a stale workflow with nothing failing.

Three properties are asserted:

- Both copies contain the same set of files.
- Every file other than ``SKILL.md`` is byte-identical.
- The Claude Code ``SKILL.md``, rewritten through
  :data:`SKILL_ADAPTATIONS`, is byte-identical to the Tabnine one.

Each adaptation must apply exactly once, so the table cannot go stale
either: a pair that no longer matches fails rather than silently becoming
a no-op, and a new difference between the copies has to be added here
explicitly, where it surfaces in review.
"""

from __future__ import annotations

import difflib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CLAUDE_SKILL = REPO_ROOT / ".claude" / "skills" / "integrate-company"
TABNINE_SKILL = REPO_ROOT / ".tabnine" / "agent" / "skills" / "integrate-company"
SKILL_FILE = "SKILL.md"

# (Claude Code text, Tabnine text) for each deliberate difference between
# the two ``SKILL.md`` copies. Each pair carries enough surrounding context
# to occur exactly once, because both copies also cite ``CLAUDE.md`` as the
# project overview, which is not an adaptation.
SKILL_ADAPTATIONS: tuple[tuple[str, str], ...] = (
    (
        "begin with `mcp__playwright__` (e.g. `mcp__playwright__browser_navigate`);\n"
        "recipes use the shorthand form.",
        "begin with `mcp_playwright_`; recipes use the shorthand form.",
    ),
    (
        "no body and no trailers, per\n   `CLAUDE.md`.",
        "no body and no trailers, per\n   `TABNINE.md`.",
    ),
)


def _relative_files(root: Path) -> set[Path]:
    return {path.relative_to(root) for path in root.rglob("*") if path.is_file()}


def _unified_diff(expected: str, actual: str) -> str:
    return "".join(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            fromfile="claude copy after adaptations",
            tofile="tabnine copy",
        )
    )


class TestSkillCopies:
    """The Tabnine copy is the Claude Code copy plus the listed adaptations."""

    def test_skill_directories_are_discoverable(self) -> None:
        """Guard against a silently-empty comparison after a move."""
        assert (CLAUDE_SKILL / SKILL_FILE).is_file(), f"missing {CLAUDE_SKILL}"
        assert (TABNINE_SKILL / SKILL_FILE).is_file(), f"missing {TABNINE_SKILL}"

    def test_both_copies_hold_the_same_files(self) -> None:
        claude_files = _relative_files(CLAUDE_SKILL)
        tabnine_files = _relative_files(TABNINE_SKILL)
        assert claude_files == tabnine_files, (
            "the skill copies hold different files: "
            f"only in .claude: {sorted(map(str, claude_files - tabnine_files))}, "
            f"only in .tabnine: {sorted(map(str, tabnine_files - claude_files))}"
        )

    def test_supporting_files_are_identical(self) -> None:
        differing = sorted(
            str(relative)
            for relative in _relative_files(CLAUDE_SKILL)
            & _relative_files(TABNINE_SKILL)
            if relative != Path(SKILL_FILE)
            and (CLAUDE_SKILL / relative).read_bytes()
            != (TABNINE_SKILL / relative).read_bytes()
        )
        assert not differing, (
            f"these files must be byte-identical in both skill copies: {differing}"
        )

    def test_skill_md_differs_only_by_the_listed_adaptations(self) -> None:
        adapted = (CLAUDE_SKILL / SKILL_FILE).read_text(encoding="utf-8")
        for claude_text, tabnine_text in SKILL_ADAPTATIONS:
            occurrences = adapted.count(claude_text)
            assert occurrences == 1, (
                f"adaptation expected exactly once in the Claude Code copy, "
                f"found {occurrences}x: {claude_text!r}"
            )
            adapted = adapted.replace(claude_text, tabnine_text)
        tabnine = (TABNINE_SKILL / SKILL_FILE).read_text(encoding="utf-8")
        assert adapted == tabnine, (
            "the skill copies have drifted; apply the change to both, or add a "
            "deliberate difference to SKILL_ADAPTATIONS:\n"
            + _unified_diff(adapted, tabnine)
        )
