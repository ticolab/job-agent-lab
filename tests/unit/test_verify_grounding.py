from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    path = ROOT / "scripts" / "verify_grounding.py"
    spec = importlib.util.spec_from_file_location("verify_grounding_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _database(*, one_shot: bool = True) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    conflicting = "conflicting_evidence_json TEXT," if one_shot else ""
    connection.executescript(
        f"""
        CREATE TABLE job_contents (
            id INTEGER PRIMARY KEY,
            text TEXT NOT NULL
        );
        CREATE TABLE job_decisions (
            id INTEGER PRIMARY KEY,
            job_id INTEGER NOT NULL,
            content_id INTEGER NOT NULL,
            {conflicting}
            evidence_json TEXT NOT NULL
        );
        CREATE TABLE job_results (
            id INTEGER PRIMARY KEY,
            job_id INTEGER NOT NULL,
            content_id INTEGER NOT NULL,
            result_json TEXT NOT NULL
        );
        """
    )
    connection.execute(
        "INSERT INTO job_contents VALUES (?, ?)",
        (1, "Build Python APIs.\nLocation: Remote \u2013 Latin America\nHybrid\nCO"),
    )
    return connection


def _decision(
    connection: sqlite3.Connection,
    supporting: object,
    conflicting: object | None,
    *,
    raw: bool = False,
) -> None:
    connection.execute(
        "INSERT INTO job_decisions "
        "(id, job_id, content_id, evidence_json, conflicting_evidence_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            1,
            7,
            1,
            supporting if raw else json.dumps(supporting),
            None if conflicting is None else json.dumps(conflicting),
        ),
    )


def test_verify_accepts_grounded_buckets_and_nested_result_evidence() -> None:
    module = _load()
    connection = _database()
    _decision(
        connection,
        ["location: remote - LATIN america"],
        ["Hybrid CO"],
    )
    connection.execute(
        "INSERT INTO job_results VALUES (?, ?, ?, ?)",
        (
            1,
            7,
            1,
            json.dumps(
                {
                    "title": {"evidence": ["BUILD PYTHON APIS."]},
                    "technologies": {
                        "value": [{"evidence": ["Python"]}],
                        "evidence": [],
                    },
                }
            ),
        ),
    )
    assert module.verify(connection) == []


def test_verify_accepts_legacy_decisions_with_null_conflicts_and_old_schema() -> None:
    module = _load()
    connection = _database()
    _decision(connection, ["Build Python APIs."], None)
    assert module.verify(connection) == []
    legacy = _database(one_shot=False)
    legacy.execute(
        "INSERT INTO job_decisions VALUES (?, ?, ?, ?)",
        (1, 7, 1, json.dumps(["Mexico only"])),
    )
    assert [miss.path for miss in module.verify(legacy)] == ["$.supporting_evidence[0]"]


def test_verify_reports_safe_misses_in_both_buckets_and_invalid_payload() -> None:
    module = _load()
    connection = _database()
    _decision(connection, ["Mexico only", "Hybrid\nCO"], ["Canada only"])
    connection.execute(
        "INSERT INTO job_results VALUES (?, ?, ?, ?)",
        (1, 7, 1, "not json"),
    )
    misses = module.verify(connection)
    assert [(miss.source, miss.path) for miss in misses] == [
        ("decision", "$.supporting_evidence[0]"),
        ("decision", "$.conflicting_evidence[0]"),
        ("result", "$"),
    ]
    digest = hashlib.sha256(b"Mexico only").hexdigest()
    assert misses[0].detail == f"not_in_posting sha256={digest} chars=11"
    assert all("Mexico only" not in miss.detail for miss in misses)
    assert "invalid payload" in misses[2].detail


@pytest.mark.parametrize(
    ("supporting", "message"),
    [("not json", "invalid payload"), ('{"a": 1}', "must be a JSON array")],
)
def test_verify_reports_malformed_decision_buckets(
    supporting: str, message: str
) -> None:
    module = _load()
    connection = _database()
    _decision(connection, supporting, [" "], raw=True)
    misses = module.verify(connection)
    assert [miss.path for miss in misses] == ["$", "$"]
    assert message in misses[0].detail
    assert "nonempty strings" in misses[1].detail


def test_verify_reports_malformed_nested_result_evidence() -> None:
    module = _load()
    connection = _database()
    _decision(connection, [], [])
    connection.execute(
        "INSERT INTO job_results VALUES (?, ?, ?, ?)",
        (1, 7, 1, json.dumps({"title": {"evidence": "Python"}})),
    )
    misses = module.verify(connection)
    assert len(misses) == 1
    assert misses[0].source == "result"
    assert misses[0].path == "$"
    assert "must be a JSON array" in misses[0].detail


def test_main_prints_no_raw_excerpts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    path = tmp_path / "grounding.db"
    source = _database()
    _decision(source, ["SECRET fabricated excerpt"], [])
    source.commit()
    disk = sqlite3.connect(path)
    source.backup(disk)
    disk.close()
    assert module.main(["--database", str(path)]) == 1
    output = capsys.readouterr().out
    assert "SECRET" not in output
    assert "path=$.supporting_evidence[0] not_in_posting sha256=" in output
    assert output.endswith("grounding_misses=1\n")


def test_pipeline_queries_execute_against_pipeline_shape() -> None:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY,
            company_slug TEXT NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE job_attempts (
            id INTEGER PRIMARY KEY,
            job_id INTEGER NOT NULL,
            run_id TEXT NOT NULL,
            outcome TEXT,
            model TEXT NOT NULL,
            prompt_tokens INTEGER,
            completion_tokens INTEGER
        );
        CREATE TABLE job_results (
            id INTEGER PRIMARY KEY,
            job_id INTEGER NOT NULL,
            result_json TEXT NOT NULL,
            is_current INTEGER NOT NULL
        );
        CREATE TABLE job_decisions (
            id INTEGER PRIMARY KEY,
            job_id INTEGER NOT NULL,
            decision TEXT NOT NULL,
            reason TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            is_current INTEGER NOT NULL,
            decided_at TEXT NOT NULL
        );
        CREATE TABLE pipeline_runs (
            id TEXT PRIMARY KEY,
            stage TEXT NOT NULL,
            trigger TEXT NOT NULL
        );
        INSERT INTO pipeline_runs VALUES ('run-1', 'eligibility', 'scheduled');
        INSERT INTO jobs VALUES (1, 'example', 'unknown');
        INSERT INTO job_attempts VALUES (1, 1, 'run-1', 'unknown', 'model', 10, 5);
        INSERT INTO job_results VALUES (
            1,
            1,
            '{"benefits":{"state":"failed","value":null,"evidence":[]}}',
            1
        );
        INSERT INTO job_decisions VALUES (
            1,
            1,
            'unknown',
            'Location is not stated.',
            'cr-2026.10.1',
            1,
            '2026-10-23'
        );
        """
    )
    sql = (ROOT / "scripts" / "pipeline_queries.sql").read_text(encoding="utf-8")
    connection.executescript(sql)
