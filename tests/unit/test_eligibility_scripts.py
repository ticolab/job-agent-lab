"""Credential-free tests for the one-shot evaluator and the expectation drafter."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from job_eligibility import (
    ELIGIBILITY_PROMPT_HASH,
    MISSING_INFORMATION_REASON,
    PROMPT_EXAMPLES,
    EligibilityResponse,
)
from job_pipeline.llm import Completion, ModelResponseError
from tests.unit.asyncio_harness import run_async

ROOT = Path(__file__).resolve().parents[2]
TEXT = "Engineer\nBogotá, Colombia\nLocation: Remote – Latin America\nRemote work.\n"


def _load(name: str, filename: str) -> ModuleType:
    path = ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _obligation(
    case_id: str, index: int, role: str, *alternatives: str
) -> dict[str, Any]:
    return {
        "obligation_id": f"{case_id}-E{index:02d}",
        "role": role,
        "critical": True,
        "excerpt_alternatives": list(alternatives),
    }


def _case(
    root: Path,
    case_id: str = "CASE01",
    *,
    decision: str = "eligible",
    must_cite: tuple[str, ...] = ("Remote work.",),
    obligations: list[dict[str, Any]] | None = None,
    pairs: list[list[str]] | None = None,
    reviewed: bool = True,
    partition: str = "development",
    prompt_example: str | None = None,
    write_evidence: bool = True,
    text: str = TEXT,
) -> Path:
    directory = root / case_id
    directory.mkdir(parents=True)
    content = text.encode()
    (directory / "content.txt").write_bytes(content)
    source_hash = hashlib.sha256(content).hexdigest()
    (directory / "expected_eligibility.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "case_id": case_id,
                "reviewed": True,
                "decision": decision,
                "must_cite": list(must_cite),
                "partition": partition,
                "source_content_sha256": source_hash,
            }
        ),
        encoding="utf-8",
    )
    if write_evidence:
        (directory / "expected_evidence.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "case_id": case_id,
                    "reviewed": reviewed,
                    "review_status": (
                        "reviewed" if reviewed else "pending_operator_review"
                    ),
                    "partition": partition,
                    "source_content_sha256": source_hash,
                    "decision": decision,
                    "prompt_example": prompt_example,
                    "missing_information": False,
                    "obligations": (
                        obligations
                        if obligations is not None
                        else [_obligation(case_id, 1, "supporting", *must_cite)]
                    ),
                    "conflict_pairs": pairs or [],
                }
            ),
            encoding="utf-8",
        )
    return directory


def _conflict_case(root: Path, case_id: str = "CONF01", **kwargs: Any) -> Path:
    return _case(
        root,
        case_id,
        decision="unknown",
        must_cite=("Bogotá, Colombia", "Location: Remote – Latin America"),
        obligations=[
            _obligation(case_id, 1, "either", "Bogotá, Colombia"),
            _obligation(
                case_id,
                2,
                "either",
                "Location: Remote – Latin America",
                "Remote – Latin America",
            ),
        ],
        pairs=[[f"{case_id}-E01", f"{case_id}-E02"]],
        **kwargs,
    )


def _response(
    decision: str = "eligible",
    supporting: tuple[str, ...] = ("Remote work.",),
    conflicting: tuple[str, ...] = (),
    reason: str = "The role is remote.",
) -> EligibilityResponse:
    return EligibilityResponse.model_validate(
        {
            "decision": decision,
            "reason": reason,
            "supporting_evidence": list(supporting),
            "conflicting_evidence": list(conflicting),
        }
    )


def _evaluate(
    module: ModuleType, case: Any, responses: Sequence[EligibilityResponse | None]
) -> list[Any]:
    return [
        module._evaluate_response(
            case, replay, module._Recorded(response, "fake", 10, 2, 5)
        )
        for replay, response in enumerate(responses, start=1)
    ]


# --- evaluator: case loading -------------------------------------------------


def test_case_loading_validates_obligations_and_marks_review_state(
    tmp_path: Path,
) -> None:
    module = _load("evaluate_leaf_case_test", "evaluate_leaf.py")
    corpus = tmp_path / "corpus"
    reviewed = _case(corpus)
    pending = _case(corpus, "CASE02", reviewed=False)
    loaded = module._load_case(reviewed, Path("CASE01"), {})
    assert loaded.expected_decision == "eligible"
    assert loaded.obligations_source == "reviewed"
    assert [o.alternatives for o in loaded.obligations] == [("Remote work.",)]
    assert module._load_case(pending, Path("CASE02"), {}).obligations_source == (
        "pending_review"
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {"obligations": [_obligation("CASE01", 1, "supporting", "Not in text")]},
            "not grounded",
        ),
        (
            {"obligations": [_obligation("CASE01", 1, "supporting", "Engineer")]},
            "drop a reviewed must_cite",
        ),
        (
            {"obligations": [_obligation("CASE01", 2, "supporting", "Remote work.")]},
            "invalid evidence obligation",
        ),
        (
            {"obligations": [_obligation("CASE01", 1, "sideways", "Remote work.")]},
            "invalid evidence obligation",
        ),
        ({"pairs": [["CASE01-E01", "CASE01-E01"]]}, "invalid conflict pairs"),
        ({"prompt_example": "bare remote with no exclusion"}, "invalid evidence"),
    ],
)
def test_case_loading_rejects_invalid_obligations(
    tmp_path: Path, kwargs: dict[str, Any], message: str
) -> None:
    module = _load("evaluate_leaf_invalid_case_test", "evaluate_leaf.py")
    directory = _case(tmp_path / "corpus", **kwargs)
    with pytest.raises(RuntimeError, match=message):
        module._load_case(directory, Path("CASE01"), {})


def test_exposed_cases_without_obligations_score_must_cite_in_either_bucket(
    tmp_path: Path,
) -> None:
    module = _load("evaluate_leaf_exposed_case_test", "evaluate_leaf.py")
    directory = _case(
        tmp_path / "corpus", partition="exposed_regression", write_evidence=False
    )
    case = module._load_case(directory, Path("CASE01"), {})
    assert case.obligations_source == "must_cite"
    assert case.obligations[0].role == "either"
    with pytest.raises(RuntimeError, match="prompt example lacks"):
        module._load_case(directory, Path("CASE01"), {"CASE01": "pattern"})


def test_prompt_example_cases_follow_the_manifest_source(tmp_path: Path) -> None:
    module = _load("evaluate_leaf_examples_test", "evaluate_leaf.py")
    corpus = ROOT / "tests" / "fixtures" / "postings" / "opportunity-screening-v2"
    # The tracked corpus uses synthetic examples: no case is excluded.
    assert module._prompt_example_cases(corpus) == {}
    assert module._prompt_example_cases(tmp_path) == {}
    patterns = {
        f"CASE{index:02d}": example.pattern
        for index, example in enumerate(PROMPT_EXAMPLES, start=1)
    }

    def manifest(source: str | None, mapping: dict[str, str]) -> None:
        section: dict[str, object] = {
            "patterns": mapping,
        }
        if source is not None:
            section["source"] = source
        (tmp_path / "manifest.json").write_text(
            json.dumps({"prompt_examples": section})
        )

    manifest("corpus", patterns)
    assert module._prompt_example_cases(tmp_path) == patterns
    manifest("synthetic", {})
    assert module._prompt_example_cases(tmp_path) == {}
    for source, mapping, message in (
        ("synthetic", patterns, "cannot name corpus cases"),
        ("corpus", {"CASE01": "unknown pattern"}, "do not match"),
        ("corpus", {}, "do not match"),
        (None, {}, "do not match"),
    ):
        manifest(source, mapping)
        with pytest.raises(RuntimeError, match=message):
            module._prompt_example_cases(tmp_path)


def test_tracked_development_corpus_loads_with_reviewed_obligations() -> None:
    module = _load("evaluate_leaf_tracked_test", "evaluate_leaf.py")
    corpus = ROOT / "tests" / "fixtures" / "postings" / "opportunity-screening-v2"
    examples = module._prompt_example_cases(corpus)
    cases = [
        module._load_case(directory, directory.relative_to(corpus), examples)
        for directory in module._directories(corpus)
    ]
    development = [case for case in cases if case.partition == "development"]
    assert len(development) == 24 and len(cases) == 32
    assert {case.obligations_source for case in development} == {"reviewed"}
    assert all(case.prompt_example is None for case in cases)
    assert {case.case_id for case in cases if case.conflict_pairs} == {
        "NBS03",
        "OCE04",
    }


# --- evaluator: evidence-role scoring ------------------------------------------


def test_obligation_roles_normalized_containment_and_conflict_pairs(
    tmp_path: Path,
) -> None:
    module = _load("evaluate_leaf_roles_test", "evaluate_leaf.py")
    case = module._load_case(_conflict_case(tmp_path / "corpus"), Path("CONF01"), {})
    split, same, wrong = _evaluate(
        module,
        case,
        [
            _response(
                "unknown",
                ("Bogotá, Colombia",),
                ("location: remote - latin america",),
            ),
            _response("unknown", ("Bogotá, Colombia", "Remote – Latin America")),
            _response("eligible", ("Remote – Latin America",)),
        ],
    )
    assert split.status == "grounded" and split.evidence_agreement
    assert split.decision_correct
    assert same.satisfied == frozenset({"CONF01-E01", "CONF01-E02"})
    assert not same.pairs_satisfied and not same.evidence_agreement
    assert not wrong.decision_correct and not wrong.evidence_agreement
    supporting = module._load_case(_case(tmp_path / "other"), Path("CASE01"), {})
    (in_conflict,) = _evaluate(
        module, supporting, [_response("eligible", ("Engineer",), ("Remote work.",))]
    )
    assert in_conflict.satisfied == frozenset()


def test_statuses_classify_invalid_inconsistent_and_ungrounded_calls(
    tmp_path: Path,
) -> None:
    module = _load("evaluate_leaf_status_test", "evaluate_leaf.py")
    case = module._load_case(_case(tmp_path / "corpus"), Path("CASE01"), {})
    results = _evaluate(
        module,
        case,
        [
            None,
            _response(supporting=("Remote work.",), conflicting=("remote  WORK.",)),
            _response(supporting=("Not in posting",)),
            _response(
                "unknown",
                supporting=(),
                reason=MISSING_INFORMATION_REASON,
            ),
        ],
    )
    assert [result.status for result in results] == [
        "invalid_response",
        "inconsistent",
        "ungrounded",
        "grounded",
    ]
    assert results[3].missing_information
    assert all(result.decision is None for result in results[:3])


# --- evaluator: metrics and gates ----------------------------------------------


def _scored_cases(
    module: ModuleType, root: Path, *, reviewed: bool = True
) -> tuple[Any, Any, Any]:
    eligible = module._load_case(
        _case(root, "ELIG01", reviewed=reviewed), Path("ELIG01"), {}
    )
    conflict = module._load_case(
        _conflict_case(root, reviewed=reviewed), Path("CONF01"), {}
    )
    example = module._load_case(
        _case(
            root,
            "EXMP01",
            reviewed=reviewed,
            prompt_example=PROMPT_EXAMPLES[0].pattern,
        ),
        Path("EXMP01"),
        {"EXMP01": PROMPT_EXAMPLES[0].pattern},
    )
    return eligible, conflict, example


def _passing_results(module: ModuleType, cases: tuple[Any, Any, Any]) -> list[Any]:
    eligible, conflict, example = cases
    return [
        *_evaluate(module, eligible, [_response()] * 5),
        *_evaluate(
            module,
            conflict,
            [
                _response(
                    "unknown",
                    ("Bogotá, Colombia",),
                    ("Location: Remote – Latin America",),
                )
            ]
            * 5,
        ),
        # A wrong example case is visible only in the secondary score.
        *_evaluate(module, example, [_response("ineligible", ("Engineer",))] * 5),
    ]


def test_development_gates_pass_on_primary_and_report_secondary(
    tmp_path: Path,
) -> None:
    module = _load("evaluate_leaf_pass_test", "evaluate_leaf.py")
    results = _passing_results(module, _scored_cases(module, tmp_path))
    report, accepted, divergent = module._eligibility_report(
        results,
        profile="development",
        dispositions={},
        input_price=1.0,
        output_price=2.0,
    )
    assert accepted, report
    assert divergent == ()
    assert "prompt_example_cases_excluded_from_primary=EXMP01" in report
    assert "[primary] cases=2 calls=10" in report
    assert "[secondary_all_including_examples] cases=3 calls=15" in report
    assert "decision_evidence_role_agreement=1.000 (10/10)" in report
    assert "critical_evidence_recall=1.000 (15/15)" in report
    assert "wrongful_ineligible=5" in report  # secondary only
    assert "prompt_tokens=150 completion_tokens=30" in report
    assert report.endswith("gates=pass\n")


@pytest.mark.parametrize(
    ("mutation", "failed"),
    [
        ("false_eligible", "false_eligible"),
        ("ungrounded", "grounding"),
        ("unstable_conflict", "safety_case_stability"),
        ("evidence_miss", "decision_evidence_role_agreement"),
    ],
)
def test_development_gates_fail_closed(
    tmp_path: Path, mutation: str, failed: str
) -> None:
    module = _load("evaluate_leaf_fail_test", "evaluate_leaf.py")
    eligible, conflict, example = _scored_cases(module, tmp_path)
    conflict_ok = _response(
        "unknown", ("Bogotá, Colombia",), ("Location: Remote – Latin America",)
    )
    conflict_responses = [conflict_ok] * 5
    eligible_responses = [_response()] * 5
    if mutation == "false_eligible":
        conflict_responses = [_response("eligible", ("Remote work.",))] * 5
    elif mutation == "ungrounded":
        eligible_responses = [_response(supporting=("Invented",))] * 5
    elif mutation == "unstable_conflict":
        conflict_responses = [conflict_ok] * 4 + [
            _response("ineligible", ("Engineer",))
        ]
    else:
        eligible_responses = [_response(supporting=("Engineer",))] * 5
    results = [
        *_evaluate(module, eligible, eligible_responses),
        *_evaluate(module, conflict, conflict_responses),
    ]
    report, accepted, divergent = module._eligibility_report(
        results,
        profile="development",
        dispositions={result.key: "accepted" for result in results},
        input_price=1.0,
        output_price=2.0,
    )
    assert not accepted
    assert failed in report.rsplit("gates=fail failed=", 1)[1]
    assert divergent


def test_grounding_gate_tolerates_one_percent_ungrounded_calls(
    tmp_path: Path,
) -> None:
    module = _load("evaluate_leaf_gate_policy_test", "evaluate_leaf.py")
    assert module.MIN_GROUNDING_RATE == 0.99
    eligible, _, _ = _scored_cases(module, tmp_path)
    results = _evaluate(
        module, eligible, [_response()] * 4 + [_response(supporting=("Invented",))]
    )
    metrics = module._scope_metrics(results)
    # The ungrounded call is not counted again as a critical-evidence miss.
    assert metrics.grounded == 4
    assert metrics.critical_total == 4 * len(eligible.obligations)
    assert metrics.critical_hits == metrics.critical_total
    for profile in ("development", "blind"):
        at_floor = replace(metrics, calls=100, grounded=99)
        assert "grounding" not in module._automated_gates(at_floor, profile)
        below = replace(metrics, calls=200, grounded=197)
        assert "grounding" in module._automated_gates(below, profile)
        empty = replace(metrics, calls=0, grounded=0)
        assert "grounding" in module._automated_gates(empty, profile)
    assert module._automated_gates(metrics, "report") == []


def test_pending_obligations_and_unreviewed_reasons_block_acceptance(
    tmp_path: Path,
) -> None:
    module = _load("evaluate_leaf_review_test", "evaluate_leaf.py")
    pending = _passing_results(module, _scored_cases(module, tmp_path, reviewed=False))
    report, accepted, _ = module._eligibility_report(
        pending,
        profile="development",
        dispositions={},
        input_price=0.0,
        output_price=0.0,
    )
    assert not accepted
    assert "obligations_pending_review=CONF01,ELIG01,EXMP01" in report
    assert report.endswith("gates=fail failed=evidence_obligations_reviewed\n")
    eligible, conflict, _ = _scored_cases(module, tmp_path / "second")
    flaky = [
        *_evaluate(module, eligible, [_response()] * 4 + [_response("unknown")]),
        *_evaluate(
            module,
            conflict,
            [
                _response(
                    "unknown",
                    ("Bogotá, Colombia",),
                    ("Location: Remote – Latin America",),
                )
            ]
            * 5,
        ),
    ]
    _, accepted, divergent = module._eligibility_report(
        flaky, profile="development", dispositions={}, input_price=0, output_price=0
    )
    assert not accepted and len(divergent) == 5
    review = module._reason_review_payload(divergent, "fake")
    assert set(review["dispositions"]) == {f"ELIG01#{n}" for n in range(1, 6)}
    assert review["eligibility_prompt_hash"] == ELIGIBILITY_PROMPT_HASH
    # Unanimity is 1/2 cases, below the 0.9 floor, even with accepted reasons.
    report, accepted, _ = module._eligibility_report(
        flaky,
        profile="development",
        dispositions={result.key: "accepted" for result in divergent},
        input_price=0,
        output_price=0,
    )
    assert not accepted
    assert "unanimous_cases" in report.rsplit("failed=", 1)[1]
    assert "manual_reason_review" not in report.rsplit("failed=", 1)[1]


def test_report_profile_never_accepts_and_blind_profile_uses_release_gates(
    tmp_path: Path,
) -> None:
    module = _load("evaluate_leaf_profiles_test", "evaluate_leaf.py")
    results = _passing_results(module, _scored_cases(module, tmp_path))
    report, accepted, _ = module._eligibility_report(
        results, profile="report", dispositions={}, input_price=0, output_price=0
    )
    assert not accepted and report.endswith("gates=report_only\n")
    _, accepted, _ = module._eligibility_report(
        results, profile="blind", dispositions={}, input_price=0, output_price=0
    )
    assert accepted


def test_dispositions_must_match_contract_and_values(tmp_path: Path) -> None:
    module = _load("evaluate_leaf_dispositions_test", "evaluate_leaf.py")
    path = tmp_path / "dispositions.json"
    payload = {
        "schema_version": 1,
        "model": "fake",
        "eligibility_prompt_hash": ELIGIBILITY_PROMPT_HASH,
        "dispositions": {"CASE01#1": {"disposition": "accepted", "note": "ok"}},
    }
    path.write_text(json.dumps(payload))
    assert module._dispositions(path, "fake") == {"CASE01#1": "accepted"}
    assert module._dispositions(None, "fake") == {}
    with pytest.raises(RuntimeError, match="do not match"):
        module._dispositions(path, "other")
    payload["dispositions"] = {"CASE01#1": {"disposition": "pending"}}
    path.write_text(json.dumps(payload))
    with pytest.raises(RuntimeError, match="accepted or rejected"):
        module._dispositions(path, "fake")


# --- evaluator: recordings, freeze, CLI ----------------------------------------


def test_recording_key_covers_model_contract_case_source_and_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load("evaluate_leaf_key_test", "evaluate_leaf.py")
    case = module._load_case(_case(tmp_path / "corpus"), Path("CASE01"), {})
    first = module._recording_key("model-a", case, 1)
    assert module._recording_key("model-a", case, 2) != first
    assert module._recording_key("model-b", case, 1) != first
    changed = type(case)(**{**case.__dict__, "source_hash": "0" * 64})
    assert module._recording_key("model-a", changed, 1) != first
    with monkeypatch.context() as patch:
        patch.setattr(module, "ELIGIBILITY_PROMPT_HASH", "changed")
        assert module._recording_key("model-a", case, 1) != first


def test_recordings_include_schema_invalid_calls_and_never_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load("evaluate_leaf_record_test", "evaluate_leaf.py")
    monkeypatch.setattr(module, "PROJECT_OUTPUT", tmp_path)
    case = module._load_case(_case(tmp_path / "corpus"), Path("CASE01"), {})
    recordings = tmp_path / "recordings"
    calls: list[int] = []
    efforts: list[Any] = []

    class Caller:
        def __init__(
            self, model: str, *, timeout: float, reasoning_effort: Any = None
        ) -> None:
            efforts.append(reasoning_effort)

        async def complete(self, messages: Any, schema: Any) -> Completion[Any]:
            calls.append(1)
            if len(calls) == 1:
                raise ModelResponseError("fake", "bad json SECRET")
            response = _response()
            return Completion(response, response.model_dump_json(), "fake", 9, 3, 4)

    monkeypatch.setattr(module, "LiteLLMCaller", Caller)

    def complete(replay: int, record: bool) -> Any:
        return run_async(
            module._eligibility_completion(
                case, replay, "fake", record, 1.0, recordings
            )
        )

    with pytest.raises(RuntimeError, match="missing or invalid recording"):
        complete(1, record=False)
    assert complete(1, record=True).parsed is None
    assert complete(2, record=True).parsed == _response()
    assert complete(1, record=True).parsed is None
    assert complete(2, record=False).prompt_tokens == 9
    assert len(calls) == 2
    assert efforts == [None, None]
    files = sorted(recordings.rglob("*.json"))
    assert len(files) == 2
    assert all((path.stat().st_mode & 0o777) == 0o600 for path in files)
    assert "SECRET" not in "".join(path.read_text() for path in files)
    envelope = json.loads(files[0].read_text())
    assert envelope["eligibility_prompt_hash"] == ELIGIBILITY_PROMPT_HASH
    envelope["eligibility_prompt_hash"] = "stale"
    files[0].write_text(json.dumps(envelope))
    stale_replay = 1 if envelope["replay"] == 1 else 2
    with pytest.raises(RuntimeError, match="incompatible recording"):
        complete(stale_replay, record=False)


def test_reasoning_effort_is_a_separate_recorded_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load("evaluate_leaf_effort_test", "evaluate_leaf.py")
    monkeypatch.setattr(module, "PROJECT_OUTPUT", tmp_path)
    case = module._load_case(_case(tmp_path / "corpus"), Path("CASE01"), {})
    plain = module._recording_key("model-a", case, 1)
    # Without an effort the key is the pre-effort key, so old recordings replay.
    assert plain == module._sha256(
        json.dumps(
            {
                "model": "model-a",
                "eligibility_prompt_hash": ELIGIBILITY_PROMPT_HASH,
                "case_id": "CASE01",
                "source_hash": case.source_hash,
                "replay": 1,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    low = module._recording_key("model-a", case, 1, "low")
    assert low not in {plain, module._recording_key("model-a", case, 1, "medium")}
    recordings = tmp_path / "recordings"
    efforts: list[Any] = []

    class Caller:
        def __init__(
            self, model: str, *, timeout: float, reasoning_effort: Any = None
        ) -> None:
            efforts.append(reasoning_effort)

        async def complete(self, messages: Any, schema: Any) -> Completion[Any]:
            response = _response()
            return Completion(response, response.model_dump_json(), "fake", 9, 3, 4)

    monkeypatch.setattr(module, "LiteLLMCaller", Caller)

    def complete(record: bool, effort: Any) -> Any:
        return run_async(
            module._eligibility_completion(
                case, 1, "fake", record, 1.0, recordings, effort
            )
        )

    assert complete(True, "low").parsed == _response()
    assert efforts == ["low"]
    assert complete(False, "low").prompt_tokens == 9
    with pytest.raises(RuntimeError, match="missing or invalid recording"):
        complete(False, None)
    (path,) = recordings.rglob("*.json")
    envelope = json.loads(path.read_text())
    assert envelope["reasoning_effort"] == "low"
    envelope["reasoning_effort"] = "high"
    path.write_text(json.dumps(envelope))
    with pytest.raises(RuntimeError, match="incompatible recording"):
        complete(False, "low")
    del envelope["reasoning_effort"]
    path.write_text(json.dumps(envelope))
    with pytest.raises(RuntimeError, match="incompatible recording"):
        complete(False, "low")

    freeze = tmp_path / "freeze.json"
    freeze.write_text(json.dumps(module._freeze_payload("fake", 5, "0" * 64, "low")))
    module._require_freeze(freeze, "fake", "low")
    for effort in (None, "medium"):
        with pytest.raises(RuntimeError, match="does not match"):
            module._require_freeze(freeze, "fake", effort)
    review = tmp_path / "review.json"
    review.write_text(json.dumps(module._reason_review_payload((), "fake", "low")))
    assert module._dispositions(review, "fake", "low") == {}
    with pytest.raises(RuntimeError, match="do not match"):
        module._dispositions(review, "fake")


def test_reasoning_effort_is_rejected_for_the_extractor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load("evaluate_leaf_effort_extractor_test", "evaluate_leaf.py")
    args = module.build_parser().parse_args(
        [
            "--leaf",
            "extractor",
            "--model",
            "fake",
            "--price-per-million",
            "1",
            "2",
            "--reasoning-effort",
            "low",
            "--recordings-dir",
            str(tmp_path / "recordings"),
        ]
    )
    monkeypatch.setattr(module, "PROJECT_OUTPUT", tmp_path)
    with pytest.raises(RuntimeError, match="only to --leaf eligibility"):
        run_async(module._run(args))


def test_blind_partition_requires_reviewed_obligations_and_a_freeze(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load("evaluate_leaf_blind_test", "evaluate_leaf.py")
    monkeypatch.setattr(module, "PROJECT_OUTPUT", tmp_path)
    corpus = tmp_path / "blind"
    reviewed = _case(corpus, "BLD01", partition="blind")
    case = module._load_case(reviewed, Path("BLD01"), {})
    assert case.partition == "blind" and case.obligations_source == "reviewed"
    bare = _case(corpus, "BLD02", partition="blind", write_evidence=False)
    with pytest.raises(RuntimeError, match="blind case lacks reviewed obligations"):
        module._load_case(bare, Path("BLD02"), {})
    args = _args(module, tmp_path, "--corpus", str(corpus), "--partition", "blind")
    with pytest.raises(RuntimeError, match="must be frozen"):
        run_async(module._run(args))


def test_freeze_record_guards_exposed_and_blind_runs(tmp_path: Path) -> None:
    module = _load("evaluate_leaf_freeze_test", "evaluate_leaf.py")
    with pytest.raises(RuntimeError, match="must be frozen"):
        module._require_freeze(None, "fake")
    path = tmp_path / "freeze.json"
    path.write_text(json.dumps(module._freeze_payload("fake", 5, "0" * 64)))
    module._require_freeze(path, "fake")
    with pytest.raises(RuntimeError, match="does not match"):
        module._require_freeze(path, "other-model")
    path.write_text(
        json.dumps(
            {**module._freeze_payload("fake", 5, "0"), "eligibility_prompt_hash": "x"}
        )
    )
    with pytest.raises(RuntimeError, match="does not match"):
        module._require_freeze(path, "fake")


def _args(module: ModuleType, tmp_path: Path, *extra: str) -> Any:
    return module.build_parser().parse_args(
        [
            "--leaf",
            "eligibility",
            "--model",
            "fake",
            "--price-per-million",
            "1",
            "2",
            "--recordings-dir",
            str(tmp_path / "recordings"),
            *extra,
        ]
    )


def test_exposed_regression_refuses_before_any_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load("evaluate_leaf_exposed_guard_test", "evaluate_leaf.py")
    monkeypatch.setattr(module, "PROJECT_OUTPUT", tmp_path)

    class Forbidden:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("no provider before the freeze check")

    monkeypatch.setattr(module, "LiteLLMCaller", Forbidden)
    for extra in (
        ("--partition", "exposed_regression", "--record"),
        ("--gate-profile", "blind", "--partition", "all", "--record"),
    ):
        with pytest.raises(RuntimeError, match="must be frozen"):
            run_async(module._run(_args(module, tmp_path, *extra)))
    with pytest.raises(RuntimeError, match="requires a development"):
        run_async(
            module._run(
                _args(
                    module,
                    tmp_path,
                    "--gate-profile",
                    "report",
                    "--write-freeze",
                    str(tmp_path / "freeze.json"),
                )
            )
        )


def test_development_run_writes_safe_report_review_and_freeze_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load("evaluate_leaf_run_test", "evaluate_leaf.py")
    monkeypatch.setattr(module, "PROJECT_OUTPUT", tmp_path)
    corpus = tmp_path / "corpus"
    _case(corpus)
    _conflict_case(corpus)
    responses = {
        "CASE01": _response(),
        "CONF01": _response(
            "unknown", ("Bogotá, Colombia",), ("Location: Remote – Latin America",)
        ),
    }

    async def fake(case: Any, replay: int, *args: object) -> Any:
        return module._Recorded(responses[case.case_id], "fake", 3, 1, 2)

    monkeypatch.setattr(module, "_eligibility_completion", fake)
    report = tmp_path / "report.txt"
    review = tmp_path / "review.json"
    freeze = tmp_path / "freeze.json"
    extra = (
        "--corpus",
        str(corpus),
        "--safe-report",
        str(report),
        "--reason-review-out",
        str(review),
        "--write-freeze",
        str(freeze),
    )
    assert run_async(module._run(_args(module, tmp_path, *extra))) == 0
    output = capsys.readouterr().out
    assert report.read_text() == output
    assert output.splitlines()[1] == "candidate model=fake reasoning_effort=none"
    assert json.loads(review.read_text())["dispositions"] == {}
    assert json.loads(review.read_text())["reasoning_effort"] is None
    frozen = json.loads(freeze.read_text())
    assert frozen["eligibility_prompt_hash"] == ELIGIBILITY_PROMPT_HASH
    assert (
        frozen["development_report_sha256"]
        == hashlib.sha256(output.encode()).hexdigest()
    )
    with pytest.raises(RuntimeError, match="already exists"):
        run_async(module._run(_args(module, tmp_path, *extra)))


def test_existing_safe_report_blocks_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load("evaluate_safe_report_reserve_test", "evaluate_leaf.py")
    monkeypatch.setattr(module, "PROJECT_OUTPUT", tmp_path)
    report = tmp_path / "report.txt"
    report.write_text("keep", encoding="utf-8")
    with pytest.raises(RuntimeError, match="already exists"):
        run_async(module._run(_args(module, tmp_path, "--safe-report", str(report))))
    assert report.read_text(encoding="utf-8") == "keep"


def test_evaluator_parser_defaults_and_replay_floor(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load("evaluate_leaf_parser_test", "evaluate_leaf.py")
    args = module.build_parser().parse_args(
        ["--leaf", "eligibility", "--model", "m", "--price-per-million", "1", "2"]
    )
    assert args.replays == 5
    assert args.partition == "development"
    assert args.gate_profile is None
    assert not hasattr(args, "fact_expectations")
    monkeypatch.setattr(
        sys,
        "argv",
        ["evaluate_leaf.py", *sys.argv[:0], "--leaf", "eligibility", "--model", "m"]
        + ["--price-per-million", "1", "2", "--replays", "4"],
    )
    assert module.main() == 2
    assert "at least 5" in capsys.readouterr().err


# --- drafter --------------------------------------------------------------------


def test_eligibility_drafts_are_credential_free_external_and_no_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load("draft_expectations_test", "draft_expectations.py")
    monkeypatch.setattr(module, "PROJECT_OUTPUT", tmp_path)

    class Forbidden:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("eligibility drafting must not call a model")

    monkeypatch.setattr(module, "LiteLLMCaller", Forbidden)
    corpus = tmp_path / "corpus"
    _case(corpus, write_evidence=False)
    _case(
        corpus,
        "CONF01",
        decision="unknown",
        must_cite=("Bogotá, Colombia", "Location: Remote – Latin America"),
        write_evidence=False,
    )
    output = tmp_path / "drafts"
    args = module.build_parser().parse_args(
        ["--corpus", str(corpus), "--output-root", str(output)]
    )
    assert run_async(module._draft(args)) == 0
    eligible = json.loads((output / "CASE01" / "expected_evidence.json").read_text())
    conflict = json.loads((output / "CONF01" / "expected_evidence.json").read_text())
    assert eligible["reviewed"] is False
    assert eligible["review_status"] == "pending_operator_review"
    assert [o["role"] for o in eligible["obligations"]] == ["supporting"]
    assert [o["role"] for o in conflict["obligations"]] == ["either", "either"]
    assert conflict["conflict_pairs"] == [["CONF01-E01", "CONF01-E02"]]
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["reviewed_count"] == 0
    assert manifest["model"] is None
    assert manifest["statuses"] == {"CASE01": "drafted", "CONF01": "drafted"}
    before = (output / "CASE01" / "expected_evidence.json").read_bytes()
    assert run_async(module._draft(args)) == 0
    assert (output / "CASE01" / "expected_evidence.json").read_bytes() == before
    assert not list(corpus.rglob("expected_evidence.json"))


def test_review_validation_accepts_reviewed_and_rejects_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load("draft_expectations_review_test", "draft_expectations.py")
    monkeypatch.setattr(module, "PROJECT_OUTPUT", tmp_path)
    corpus = tmp_path / "corpus"
    _case(corpus, write_evidence=False)
    review = tmp_path / "review"
    draft = module._obligation_draft(
        corpus / "CASE01", (corpus / "CASE01" / "content.txt").read_bytes()
    )
    reviewed = {
        **draft,
        "reviewed": True,
        "review_status": "reviewed",
        "review_provenance": {
            "operator": "tester",
            "reviewed_at": "2026-11-14T00:00:00Z",
            "basis": "manual review",
        },
    }

    def write(payload: dict[str, Any]) -> None:
        (review / "CASE01").mkdir(parents=True, exist_ok=True)
        (review / "CASE01" / "expected_evidence.json").write_text(json.dumps(payload))

    write(reviewed)
    assert module.validate_review_dir(corpus, review) == 0
    for broken in (
        draft,
        {**reviewed, "decision": "unknown"},
        {
            **reviewed,
            "obligations": [_obligation("CASE01", 1, "supporting", "Engineer")],
        },
        {
            **reviewed,
            "obligations": [_obligation("CASE01", 1, "supporting", "Invented")],
        },
        {**reviewed, "conflict_pairs": [["CASE01-E01", "CASE01-E02"]]},
        {**reviewed, "review_provenance": {"operator": "tester"}},
    ):
        write(broken)
        with pytest.raises(RuntimeError):
            module.validate_review_dir(corpus, review)
    write(reviewed)
    (review / "extra.json").write_text("{}")
    with pytest.raises(RuntimeError, match="exactly match"):
        module.validate_review_dir(corpus, review)


def test_draft_case_selection_is_ordered_and_rejects_unknown(tmp_path: Path) -> None:
    module = _load("draft_expectations_selection_test", "draft_expectations.py")
    corpus = tmp_path / "corpus"
    for case_id in ("A01", "B01", "C01"):
        _case(corpus, case_id, write_evidence=False)
    assert [
        directory.name
        for directory in module._selected_directories(corpus, ("C01", "A01", "C01"))
    ] == ["C01", "A01"]
    with pytest.raises(RuntimeError, match="unknown case ids: Z99"):
        module._selected_directories(corpus, ("Z99",))


def test_model_path_component_is_hashed_and_contained(tmp_path: Path) -> None:
    draft = _load("draft_expectations_model_path_test", "draft_expectations.py")
    evaluate = _load("evaluate_leaf_model_path_test", "evaluate_leaf.py")
    draft.__dict__["PROJECT_OUTPUT"] = tmp_path
    evaluate.__dict__["PROJECT_OUTPUT"] = tmp_path
    assert draft._model_key("..") != ".."
    assert evaluate._model_key("../escape") != "../escape"
    root = tmp_path / "recordings"
    child = draft._output_child(root, draft._model_key(".."), "recording.json")
    assert child.is_relative_to(root)
    with pytest.raises(RuntimeError, match="escapes output"):
        draft._output_child(root, "..", "escaped.json")


def test_paths_must_be_under_project_output(tmp_path: Path) -> None:
    draft = _load("draft_expectations_path_test", "draft_expectations.py")
    evaluate = _load("evaluate_leaf_path_test", "evaluate_leaf.py")
    with pytest.raises(RuntimeError, match="ignored output"):
        draft._require_output_path(tmp_path, "draft output")
    with pytest.raises(RuntimeError, match="ignored output"):
        evaluate._require_output_path(tmp_path, "recording output")
    corpus = tmp_path / "corpus"
    _case(corpus)
    with pytest.raises(RuntimeError, match="ignored output"):
        draft.validate_review_dir(corpus, tmp_path / "review")


# --- extractor path (unchanged by SYS-2028) -----------------------------------


def test_extractor_mode_still_skips_reviewed_ineligible(tmp_path: Path) -> None:
    draft = _load("draft_expectations_filter_test", "draft_expectations.py")
    evaluator = _load("evaluate_leaf_filter_test", "evaluate_leaf.py")
    eligible = _case(tmp_path, "eligible", write_evidence=False)
    ineligible = _case(
        tmp_path,
        "ineligible",
        decision="ineligible",
        must_cite=("Engineer",),
        write_evidence=False,
    )
    assert draft._extractor_directories([eligible, ineligible]) == [eligible]
    assert evaluator._extractor_directories([eligible, ineligible]) == [eligible]


def test_extractor_expectations_preload_before_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load("evaluate_extractor_preload_test", "evaluate_leaf.py")
    monkeypatch.setattr(module, "PROJECT_OUTPUT", tmp_path)
    corpus = tmp_path / "corpus"
    _case(corpus)
    expectations = tmp_path / "expectations" / "CASE01"
    expectations.mkdir(parents=True)
    (expectations / "expected_extraction.json").write_text("not-json")

    class ForbiddenCaller:
        def __init__(self, model: str, *, timeout: float) -> None:
            raise AssertionError(
                "expectations must validate before provider construction"
            )

    monkeypatch.setattr(module, "LiteLLMCaller", ForbiddenCaller)
    args = module.build_parser().parse_args(
        [
            "--leaf",
            "extractor",
            "--model",
            "fake",
            "--record",
            "--price-per-million",
            "1",
            "2",
            "--corpus",
            str(corpus),
            "--recordings-dir",
            str(tmp_path / "recordings"),
            "--extractor-expectations",
            str(tmp_path / "expectations"),
        ]
    )
    with pytest.raises(RuntimeError, match="missing or invalid"):
        run_async(module._run(args))


def test_extractor_evaluator_thresholds(capsys: pytest.CaptureFixture[str]) -> None:
    module = _load("evaluate_extractor_threshold_test", "evaluate_leaf.py")
    result = module.ExtractorEvaluation(
        expected={
            "metadata": {"title": {"state": "extracted", "value": "Engineer"}},
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
