"""Generic main-content extraction and shared text normalization."""

from __future__ import annotations

import re
import unicodedata
from html.parser import HTMLParser

from trafilatura import extract

CLOSED_PHRASES = (
    "no longer accepting applications",  # LinkedIn
    "this job is no longer available",  # Workday
    "this position has been filled",  # custom boards
    "the job you are looking for is no longer open",  # iCIMS
    "ya no se aceptan solicitudes",  # LinkedIn Spanish
    "este empleo ya no está disponible",  # Workday Spanish
    "esta posición ha sido cubierta",  # custom boards in Spanish
    "el empleo que buscas ya no está disponible",  # iCIMS Spanish
)

_HORIZONTAL_WHITESPACE = re.compile(r"[^\S\n]+")
_EXCESS_NEWLINES = re.compile(r"\n{3,}")
_LIST_ITEM = re.compile(r"(?i)(<li(?:\s[^>]*)?>)")


class _TitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._inside_title = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() == "title":
            self._inside_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "title":
            self._inside_title = False

    def handle_data(self, data: str) -> None:
        if self._inside_title:
            self.parts.append(data)


def normalize(text: str) -> str:
    """Normalize Unicode and whitespace without discarding paragraph boundaries."""
    value = (
        unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
    )
    value = "\n".join(
        _HORIZONTAL_WHITESPACE.sub(" ", line).strip() for line in value.split("\n")
    )
    return _EXCESS_NEWLINES.sub("\n\n", value).strip()


def _page_title(html: str) -> str | None:
    parser = _TitleParser()
    parser.feed(html)
    title = normalize(" ".join(parser.parts))
    return title or None


def main_text(html: str, url: str) -> tuple[str, str | None]:
    """Extract normalized plain text and the document title."""
    extracted = extract(
        _LIST_ITEM.sub(r"\1• ", html),
        url=url,
        include_tables=True,
        include_links=False,
        favor_recall=True,
        output_format="txt",
    )
    return normalize(extracted or ""), _page_title(html)


def looks_closed(text: str) -> bool:
    """Return whether normalized text carries a known explicit closure phrase."""
    folded = normalize(text).casefold()
    return any(phrase.casefold() in folded for phrase in CLOSED_PHRASES)
