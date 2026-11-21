"""Text normalization shared by both sides of evidence grounding.

Only Unicode compatibility forms, line endings, whitespace, case, dash
characters, and approved layout separators normalize. Nothing here interprets
geography: the one-shot contract leaves that meaning to the model and verifies
only that each quoted excerpt occurs in the posting.
"""

from __future__ import annotations

import re
import unicodedata

_DASH_CHARACTERS = frozenset("‐‑‒–—―−﹘﹣－")
_SEPARATOR_CHARACTERS = frozenset("·•‧∙⋅・･|｜¦❘")


def _normalize_layout_characters(text: str) -> str:
    normalized: list[str] = []
    for character in unicodedata.normalize("NFKC", text):
        if character in _DASH_CHARACTERS:
            normalized.append("-")
        elif character in _SEPARATOR_CHARACTERS:
            normalized.append(" · ")
        elif unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"}:
            normalized.append("\n" if character in {"\n", "\r"} else " ")
        else:
            normalized.append(character)
    return "".join(normalized)


def normalize_text(text: str) -> str:
    """Normalize Unicode, line endings, whitespace runs, and case."""
    value = (
        unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
    )
    value = "\n".join(" ".join(line.split()) for line in value.split("\n"))
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    return value.casefold()


def grounding_text(text: str) -> str:
    """Return the normalized form both a quote and its posting are compared in.

    Layout separators and dash variants collapse to one canonical character and
    every whitespace run, including line breaks, becomes one space. Word order,
    spelling, and punctuation are preserved, so a paraphrase, a reordered quote,
    or text combined from separated passages still fails to match.
    """
    return " ".join(normalize_text(_normalize_layout_characters(text)).split())


def occurs_in(quote: str, posting_text: str) -> bool:
    """Whether *quote* is a normalized-exact contiguous substring of the posting."""
    normalized = grounding_text(quote)
    return bool(normalized) and normalized in grounding_text(posting_text)
