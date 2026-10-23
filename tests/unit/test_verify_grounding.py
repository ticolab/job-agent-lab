from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    path = ROOT / "scripts" / "verify_grounding.py"
    spec = importlib.util.spec_from_file_location("verify_grounding_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE job_contents (
            id INTEGER PRIMARY KEY,
            text TEXT NOT NULL
        );
        CREATE TABLE job_decisions (
            id INTEGER PRIMARY KEY,
            job_id INTEGER NOT NULL,
            content_id INTEGER NOT NULL,
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
        (1, "Build Python APIs.\nWork from Costa Rica."),
    )
    return connection


def test_verify_accepts_nested_grounded_evidence() -> None:
    module = _load()
    connection = _database()
    connection.execute(
        "INSERT INTO job_decisions VALUES (?, ?, ?, ?)",
        (1, 7, 1, json.dumps(["work  from Costa Rica"])),
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


def test_verify_reports_miss_and_invalid_payload() -> None:
    module = _load()
    connection = _database()
    connection.execute(
        "INSERT INTO job_decisions VALUES (?, ?, ?, ?)",
        (1, 7, 1, json.dumps(["Mexico only"])),
    )
    connection.execute(
        "INSERT INTO job_results VALUES (?, ?, ?, ?)",
        (1, 7, 1, "not json"),
    )
    misses = module.verify(connection)
    assert [(miss.source, miss.path) for miss in misses] == [
        ("decision", "$.evidence[0]"),
        ("result", "$"),
    ]


def test_verify_reports_malformed_nested_evidence() -> None:
    module = _load()
    connection = _database()
    connection.execute(
        "INSERT INTO job_decisions VALUES (?, ?, ?, ?)",
        (1, 7, 1, "[]"),
    )
    connection.execute(
        "INSERT INTO job_results VALUES (?, ?, ?, ?)",
        (1, 7, 1, json.dumps({"title": {"evidence": "Python"}})),
    )
    misses = module.verify(connection)
    assert len(misses) == 1
    assert misses[0].source == "result"
    assert misses[0].path == "$"
    assert "must be a JSON array" in misses[0].quote


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
