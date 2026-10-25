"""Verify that persisted evidence quotes occur in their stored posting text."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from job_discovery.settings import DATABASE_PATH

SourceKind = Literal["decision", "result"]


@dataclass(frozen=True)
class GroundingMiss:
    source: SourceKind
    row_id: int
    job_id: int
    content_id: int
    path: str
    quote: str


def normalize(text: str) -> str:
    """Apply the leaves' shared grounding normalization."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


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


def _decision_quotes(value: Any) -> Iterator[tuple[str, str]]:
    if not isinstance(value, list):
        raise ValueError("decision evidence must be a JSON array")
    for index, quote in enumerate(value):
        if not isinstance(quote, str) or not quote.strip():
            raise ValueError("decision evidence entries must be nonempty strings")
        yield f"$.evidence[{index}]", quote


def _check_rows(
    connection: sqlite3.Connection,
    *,
    source: SourceKind,
    table: str,
    payload_column: str,
) -> list[GroundingMiss]:
    query = f"""
        SELECT source.id, source.job_id, source.content_id,
               source.{payload_column}, content.text
        FROM {table} AS source
        JOIN job_contents AS content ON content.id = source.content_id
        ORDER BY source.id
    """
    misses: list[GroundingMiss] = []
    for row_id, job_id, content_id, raw_payload, posting_text in connection.execute(
        query
    ):
        try:
            payload = json.loads(raw_payload)
            quotes = (
                _decision_quotes(payload)
                if source == "decision"
                else _quote_lists(payload)
            )
            normalized_posting = normalize(posting_text)
            for path, quote in quotes:
                if normalize(quote) not in normalized_posting:
                    misses.append(
                        GroundingMiss(
                            source=source,
                            row_id=row_id,
                            job_id=job_id,
                            content_id=content_id,
                            path=path,
                            quote=quote,
                        )
                    )
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            misses.append(
                GroundingMiss(
                    source=source,
                    row_id=row_id,
                    job_id=job_id,
                    content_id=content_id,
                    path="$",
                    quote=f"invalid payload: {exc}",
                )
            )
    return misses


def verify(connection: sqlite3.Connection) -> list[GroundingMiss]:
    """Return every ungrounded or malformed persisted evidence value."""
    misses = _check_rows(
        connection,
        source="decision",
        table="job_decisions",
        payload_column="evidence_json",
    )
    misses.extend(
        _check_rows(
            connection,
            source="result",
            table="job_results",
            payload_column="result_json",
        )
    )
    return misses


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check persisted decision and result evidence grounding."
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
            f"content={miss.content_id} path={miss.path} quote={miss.quote!r}"
        )
    print(f"grounding_misses={len(misses)}")
    return int(bool(misses))


if __name__ == "__main__":
    raise SystemExit(main())
