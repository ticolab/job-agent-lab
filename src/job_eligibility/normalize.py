"""Word-level matching shared by both sides of evidence grounding.

A quote is grounded when its words occur in the posting as one unbroken run of
whole words, in the same order. Case, punctuation, symbols, and whitespace are
ignored, so an added period, a dropped list dash, or a reflowed line does not
reject a correct quote, while a paraphrase, a reordered quote, or text stitched
from separate passages still fails. Nothing here interprets geography: the
contract leaves that meaning to the model and verifies only that each quoted
excerpt occurs in the posting.
"""

from __future__ import annotations

import re
import unicodedata

# A word is a run of letters and digits, with any combining accents that
# follow them; underscores count as punctuation.
_WORD = re.compile(r"[^\W_]+(?:[\u0300-\u036f]+[^\W_]*)*")

WordSpan = tuple[str, int, int]


def word_spans(text: str) -> list[WordSpan]:
    """Return each word, compatibility-normalized and casefolded, with its span.

    Spans index the original text, so a located quote can be returned as the
    posting's own characters.
    """
    spans: list[WordSpan] = []
    for match in _WORD.finditer(text):
        normalized = unicodedata.normalize("NFKC", match.group()).casefold()
        # Compatibility normalization can introduce punctuation; keep the words.
        for word in _WORD.findall(normalized):
            spans.append((word, match.start(), match.end()))
    return spans


def words(text: str) -> list[str]:
    """Return the normalized words of *text* in order."""
    return [word for word, _, _ in word_spans(text)]


def word_key(text: str) -> str:
    """Return a key equal for two texts exactly when their words are equal."""
    return " ".join(words(text))


def _find(needle: list[str], haystack: list[str]) -> int | None:
    if not needle or len(needle) > len(haystack):
        return None
    first = needle[0]
    for start in range(len(haystack) - len(needle) + 1):
        if haystack[start] == first and haystack[start : start + len(needle)] == needle:
            return start
    return None


def locate_words(quote: str, posting_spans: list[WordSpan]) -> tuple[int, int] | None:
    """Return the character span of the first run of *quote*'s words, if any."""
    needle = words(quote)
    start = _find(needle, [word for word, _, _ in posting_spans])
    if start is None:
        return None
    return posting_spans[start][1], posting_spans[start + len(needle) - 1][2]


def locate(quote: str, posting_text: str) -> tuple[int, int] | None:
    """Return where *quote*'s words occur in the posting as one unbroken run."""
    return locate_words(quote, word_spans(posting_text))


def occurs_in(quote: str, posting_text: str) -> bool:
    """Whether *quote*'s words occur in the posting as one unbroken run."""
    return locate(quote, posting_text) is not None


def contains_words(text: str, phrase: str) -> bool:
    """Whether *phrase*'s words occur in *text* as one unbroken run."""
    return _find(words(phrase), words(text)) is not None
