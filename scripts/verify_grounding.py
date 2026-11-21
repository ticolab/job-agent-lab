"""Verify that persisted evidence excerpts occur in their stored posting text.

Decision rows are checked bucket by bucket (supporting evidence in
``evidence_json``, conflicting evidence in ``conflicting_evidence_json``) with
the eligibility leaf's normalized-exact grounding. Extraction results are
checked with the extractor's whitespace and case normalization. A miss reports
its location, a SHA-256 digest, and a character count, never the excerpt or
posting text itself. The database is opened read-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from job_discovery.settings import DATABASE_PATH
from job_eligibility import grounding_text

SourceKind = Literal["decision", "result"]


@dataclass(frozen=True)
class GroundingMiss:
    source: SourceKind
    row_id: int
    job_id: int
    content_id: int
    path: str
    detail: str


def normalize(text: str) -> str:
    """Apply the extractor's grounding normalization."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


_Row = tuple[int, int, int]


def _miss(source: SourceKind, row: _Row, path: str, detail: str) -> GroundingMiss:
    row_id, job_id, content_id = row
    return GroundingMiss(source, row_id, job_id, content_id, path, detail)


def _safe_detail(value: str) -> str:
    digest = hashlib.sha256(value.encode()).hexdigest()
    return f"not_in_posting sha256={digest} chars={len(value)}"


def _quote_lists(value: Any, path: str = "$") -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key == "evidence":
                if not isinstance(child, list):
                    raise ValueError(f"{child_path} must be a JSON array")
                for index, quote in enumerate(child):
                    if not isinstance(quote, str) or not quote.strip():
                        raise ValueError(
                            f"{child_path}[{index}] must be a nonempty string"
                        )
                    yield f"{child_path}[{index}]", quote
            else:
                yield from _quote_lists(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _quote_lists(child, f"{path}[{index}]")


def _bucket(raw: str | None, name: str) -> Iterator[tuple[str, str]]:
    if raw is None:
        return
    values = json.loads(raw)
    if not isinstance(values, list):
        raise ValueError(f"{name} must be a JSON array")
    for index, quote in enumerate(values):
        if not isinstance(quote, str) or not quote.strip():
            raise ValueError(f"{name} entries must be nonempty strings")
        yield f"$.{name}[{index}]", quote


def _decision_misses(connection: sqlite3.Connection) -> list[GroundingMiss]:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(job_decisions)")}
    conflicting = (
        "source.conflicting_evidence_json"
        if "conflicting_evidence_json" in columns
        else "NULL"
    )
    query = f"""
        SELECT source.id, source.job_id, source.content_id,
               source.evidence_json, {conflicting}, content.text
        FROM job_decisions AS source
        JOIN job_contents AS content ON content.id = source.content_id
        ORDER BY source.id
    """
    misses: list[GroundingMiss] = []
    for row_id, job_id, content_id, supporting, opposing, text in connection.execute(
        query
    ):
        row = (row_id, job_id, content_id)
        posting = grounding_text(text)
        for name, raw in (
            ("supporting_evidence", supporting),
            ("conflicting_evidence", opposing),
        ):
            try:
                quotes = list(_bucket(raw, name))
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                misses.append(_miss("decision", row, "$", f"invalid payload: {exc}"))
                continue
            for path, quote in quotes:
                normalized = grounding_text(quote)
                if not normalized or normalized not in posting:
                    misses.append(_miss("decision", row, path, _safe_detail(quote)))
    return misses


def _result_misses(connection: sqlite3.Connection) -> list[GroundingMiss]:
    query = """
        SELECT source.id, source.job_id, source.content_id,
               source.result_json, content.text
        FROM job_results AS source
        JOIN job_contents AS content ON content.id = source.content_id
        ORDER BY source.id
    """
    misses: list[GroundingMiss] = []
    for row_id, job_id, content_id, raw_payload, text in connection.execute(query):
        row = (row_id, job_id, content_id)
        try:
            posting = normalize(text)
            found = [
                _miss("result", row, path, _safe_detail(quote))
                for path, quote in _quote_lists(json.loads(raw_payload))
                if normalize(quote) not in posting
            ]
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            found = [_miss("result", row, "$", f"invalid payload: {exc}")]
        misses.extend(found)
    return misses


def verify(connection: sqlite3.Connection) -> list[GroundingMiss]:
    """Return every ungrounded or malformed persisted evidence value."""
    return [*_decision_misses(connection), *_result_misses(connection)]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Check persisted decision (supporting and conflicting) and result "
            "evidence grounding."
        )
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DATABASE_PATH,
        help=f"SQLite database path (default: {DATABASE_PATH})",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.database.is_file():
        print(f"database does not exist: {args.database}", file=sys.stderr)
        return 2
    connection = sqlite3.connect(f"file:{args.database}?mode=ro", uri=True)
    try:
        misses = verify(connection)
    except sqlite3.Error as exc:
        print(f"cannot verify grounding: {exc}", file=sys.stderr)
        return 2
    finally:
        connection.close()
    for miss in misses:
        print(
            f"{miss.source} row={miss.row_id} job={miss.job_id} "
            f"content={miss.content_id} path={miss.path} {miss.detail}"
        )
    print(f"grounding_misses={len(misses)}")
    return int(bool(misses))


if __name__ == "__main__":
    raise SystemExit(main())
