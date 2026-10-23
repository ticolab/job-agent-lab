"""Pure tests for eligibility expectation and evaluation script safeguards."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, filename: str) -> ModuleType:
    path = ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_draft_script_reports_missing_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load("draft_expectations_test", "draft_expectations.py")
    monkeypatch.setattr(module, "POSTINGS", ROOT / "does-not-exist")
    assert module._posting_directories() == []


def test_evaluator_refuses_unreviewed_expectation(tmp_path: Path) -> None:
    module = _load("evaluate_leaf_test", "evaluate_leaf.py")
    path = tmp_path / "expected_eligibility.json"
    path.write_text(
        json.dumps(
            {
                "reviewed": False,
                "decision": "eligible",
                "must_cite": ["Costa Rica"],
                "notes": "draft",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="not reviewed"):
        module._expectation(tmp_path)


def test_evaluator_requires_nonempty_must_cite_alternatives(tmp_path: Path) -> None:
    module = _load("evaluate_leaf_must_cite_contract_test", "evaluate_leaf.py")
    (tmp_path / "expected_eligibility.json").write_text(
        json.dumps({"reviewed": True, "decision": "eligible", "must_cite": []}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="invalid eligibility expectation"):
        module._expectation(tmp_path)


def test_must_cite_uses_grounding_normalization() -> None:
    module = _load("evaluate_leaf_must_cite_test", "evaluate_leaf.py")
    assert module._matches_must_cite(
        ["Role is REMOTE\u00a0  in Costa Rica."],
        ["remote in costa rica"],
    )
    assert not module._matches_must_cite(
        ["Remote throughout Canada"],
        ["Costa Rica", "Latin America"],
    )


def test_must_cite_failure_rejects_matching_decision(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load("evaluate_leaf_must_cite_report_test", "evaluate_leaf.py")
    result = module.Evaluation(
        expected="eligible",
        actual="eligible",
        reason="The location qualifies.",
        grounding_failed=False,
        must_cite_matched=False,
        prompt_tokens=10,
        completion_tokens=5,
        latency_ms=50,
        name="posting",
    )
    assert not module._print_report([result], 1.0, 2.0)
    output = capsys.readouterr().out
    assert "must_cite_mismatch posting" in output
    assert "must_cite_failures=1" in output


def test_latency_aggregates_total_and_per_posting() -> None:
    module = _load("evaluate_leaf_latency_test", "evaluate_leaf.py")

    class Result:
        def __init__(self, latency_ms: int) -> None:
            self.latency_ms = latency_ms

    assert module._latency([Result(100), Result(201)]) == (
        "latency_ms_total=301 latency_ms_per_posting=150.5"
    )


def test_draft_parser_dispatches_both_leaves() -> None:
    module = _load("draft_expectations_parser_test", "draft_expectations.py")
    parser = module.build_parser()
    assert parser.parse_args(["--leaf", "extractor", "--model", "test"]).leaf == (
        "extractor"
    )


def test_evaluator_acceptance_thresholds(capsys: pytest.CaptureFixture[str]) -> None:
    module = _load("evaluate_leaf_threshold_test", "evaluate_leaf.py")
    result = module.Evaluation(
        expected="eligible",
        actual="ineligible",
        reason="The role is elsewhere.",
        grounding_failed=False,
        must_cite_matched=True,
        prompt_tokens=10,
        completion_tokens=5,
        latency_ms=125,
        name="posting",
    )
    assert not module._print_report([result], 1.0, 2.0)
    output = capsys.readouterr().out
    assert "mismatch posting" in output
    assert "latency_ms_total=125 latency_ms_per_posting=125.0" in output


def test_extractor_dispatch_skips_reviewed_ineligible_postings(
    tmp_path: Path,
) -> None:
    draft = _load("draft_expectations_filter_test", "draft_expectations.py")
    evaluator = _load("evaluate_leaf_filter_test", "evaluate_leaf.py")
    eligible = tmp_path / "eligible"
    ineligible = tmp_path / "ineligible"
    eligible.mkdir()
    ineligible.mkdir()
    (eligible / "expected_eligibility.json").write_text(
        json.dumps({"reviewed": True, "decision": "eligible", "must_cite": ["Remote"]}),
        encoding="utf-8",
    )
    (ineligible / "expected_eligibility.json").write_text(
        json.dumps(
            {"reviewed": True, "decision": "ineligible", "must_cite": ["Canada"]}
        ),
        encoding="utf-8",
    )
    directories = [eligible, ineligible]
    assert draft._extractor_directories(directories) == [eligible]
    assert evaluator._extractor_directories(directories) == [eligible]


def test_extractor_evaluator_thresholds(capsys: pytest.CaptureFixture[str]) -> None:
    module = _load("evaluate_extractor_threshold_test", "evaluate_leaf.py")
    result = module.ExtractorEvaluation(
        expected={
            "metadata": {
                "title": {"state": "extracted", "value": "Engineer"},
                "work_mode": {"state": "extracted", "value": "remote"},
                "employment_type": {"state": "not_stated", "value": None},
                "experience_level": {"state": "not_stated", "value": None},
            },
            "responsibilities": [],
            "required_qualifications": [],
            "preferred_qualifications": [],
            "benefits": [],
            "technologies": [],
        },
        actual=None,
        grounding_failed=True,
        prompt_tokens=10,
        completion_tokens=5,
        latency_ms=250,
        name="posting",
    )
    assert not module._print_extractor_report([result], 1.0, 2.0)
    output = capsys.readouterr().out
    assert "grounding_failures=1" in output
    assert "latency_ms_total=250 latency_ms_per_posting=250.0" in output
