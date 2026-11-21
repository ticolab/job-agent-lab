from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from job_eligibility import (
    PROMPT_EXAMPLES,
    occurs_in,
)

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "tests/fixtures/postings/opportunity-screening-v2"
EXPECTED_V1_CASES_HASH = (
    "1fb35e4cb7d07fb2545352ae75e04f858452d08c6ecc956bb69a165424561d6d"
)
EXPECTED_V1_MANIFEST_HASH = (
    "9ad2acde29eaa5347dfd9f94acc846bf6a4045374451e85787067dcff9998eef"
)
EXPECTED_CORPUS_HASH = (
    "1cb5db293ea719c00ffa2b5d2de19b011ac1bfcfc759ad69c8df9c65a0051449"
)
PRE_REMOVAL_CORPUS_HASH = (
    "e37dd023e41430a667f00b1c385df52d3ec1a364653d05683423855fd46a43c9"
)
PRE_REMOVAL_MANIFEST_HASH = (
    "50c737d702e69a22bebb5e65977b236b3cecd24e734a53cee9645529cfcfa1d2"
)
PRE_SYNTHETIC_CORPUS_HASH = (
    "72d011f4198f4620d6bf744994efd5e22b8eab3cb4f24aa8c905d1e9a01270b8"
)
PRE_SYNTHETIC_MANIFEST_HASH = (
    "e0003a1ee5ac57bada08c58fc164d4e6161d14abe868446f0a68b1cf99859f4d"
)
PRE_OBLIGATION_REVISION_CORPUS_HASH = (
    "a0f8628edd5d55388b135958bb01c7cb987755b55e7f43041f0b3b751e7a6a93"
)
PRE_OBLIGATION_REVISION_MANIFEST_HASH = (
    "efc56669b38f072eb8eb32efc961a89e7174309d9e0fecac1b5d6de5910c761a"
)
# The v1 prompt examples were derived from these development cases. The v2
# examples are synthetic, so the mapping survives only as manifest history.
V1_PROMPT_EXAMPLE_CASES = {
    "GOL01": "bare remote with no exclusion",
    "GOL03": "explicit remote limited to foreign-only alternatives",
    "JOB02": "foreign base and foreign authorization with contextual travel",
    "NBS03": "broad LATAM permission conflicting with a concrete foreign location",
    "OCE04": "ambiguous location code with a hybrid and remote conflict",
    "JOB03": "timezone requirement without residence permission",
}
EXPECTED_DECISIONS = {"eligible": 19, "ineligible": 7, "unknown": 6}
EXPECTED_DEVELOPMENT = {
    "AKU01",
    "AKU03",
    "AKU04",
    "BAJ02",
    "BAJ03",
    "BAJ04",
    "GOL01",
    "GOL02",
    "GOL03",
    "JOB01",
    "JOB02",
    "JOB03",
    "KAK01",
    "KAK02",
    "KAK03",
    "NBS02",
    "NBS03",
    "NBS04",
    "OCE01",
    "OCE02",
    "OCE04",
    "RAI01",
    "RAI02",
    "RAI03",
}
EXPECTED_EXPOSED_REGRESSION = {
    "AKU02",
    "BAJ01",
    "GOL04",
    "JOB04",
    "KAK04",
    "NBS01",
    "OCE03",
    "RAI04",
}
EXPECTED_EXPECTATION_KEYS = {
    "schema_version",
    "case_id",
    "reviewed",
    "decision",
    "must_cite",
    "rationale",
    "partition",
    "source_content_sha256",
    "provenance",
    "review_provenance",
    "lineage",
}
EXPECTED_EVIDENCE_KEYS = {
    "schema_version",
    "case_id",
    "reviewed",
    "review_status",
    "partition",
    "source_content_sha256",
    "decision",
    "prompt_example",
    "missing_information",
    "obligations",
    "conflict_pairs",
}
EXPECTED_OBLIGATION_KEYS = {
    "obligation_id",
    "role",
    "critical",
    "excerpt_alternatives",
}
DEVELOPMENT_FILES = (
    "content.txt",
    "expected_eligibility.json",
    "expected_evidence.json",
)
FORBIDDEN_NAMES = {
    "completion",
    "model",
    "model_response",
    "provider",
    "raw_provider",
    "raw_response",
    "response",
    "responses",
}
SECRET_PATTERNS = (
    re.compile(
        rb"(?i)(?:access[_-]?token|api[_-]?key|client[_-]?secret|password)"
        rb"\s*[=:]\s*[^\s\"'&<>]+"
    ),
    re.compile(
        rb"(?i)(?:[?&](?:token|access_token|api_key|key|secret)=)"
        rb"(?!REDACTED(?:[&#]|$))[^&#\s\"'<>]+"
    ),
    re.compile(
        rb"(?i)(?:authorization\s*[=:]\s*(?:bearer|basic)\s+)"
        rb"[A-Za-z0-9._~+/-]+=*"
    ),
    re.compile(rb"(?i)(?:set-cookie|cookie)\s*:"),
)
FORBIDDEN_FIELD_PATTERN = re.compile(
    rb'(?i)"(?:draft[^"\\]*|raw_[^"\\]*|completion|model|model_response|provider|response|responses)"\s*:'
)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _payload(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _normalize_evidence(value: str) -> str:
    return " ".join(value.split()).casefold()


def _development_literal_hashes(case_ids: set[str]) -> dict[str, str]:
    return {
        f"{case_id}/{filename}": _sha256((CORPUS / case_id / filename).read_bytes())
        for case_id in case_ids
        for filename in DEVELOPMENT_FILES
    }


def _assert_no_forbidden_keys(value: object) -> None:
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key in current:
                assert isinstance(key, str)
                folded = key.casefold()
                assert folded not in FORBIDDEN_NAMES
                assert not folded.startswith(("draft_", "raw_"))
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)


def test_manifest_records_fact_removal_obligations_and_v1_lineage() -> None:
    manifest = _payload(CORPUS / "manifest.json")
    assert manifest["schema_version"] == 1
    assert manifest["corpus_id"] == "opportunity-screening-v2"
    assert "facts" not in manifest
    assert "facts_status" not in manifest
    assert manifest["evidence_obligations"] == {
        "filename": "expected_evidence.json",
        "status": "operator_reviewed",
        "review_provenance": manifest["evidence_obligations"]["review_provenance"],
        "derivation": manifest["evidence_obligations"]["derivation"],
        "development": {"cases": 24, "files": 24},
        "exposed_regression": {"cases": 8, "files": 0, "status": "not_authored"},
    }
    assert "operator review" in manifest["evidence_obligations"]["derivation"]
    assert manifest["prompt_examples"] == {
        "source": "synthetic",
        "authorization": (
            "user decision recorded after the SYS-2028 development evaluation"
        ),
        "patterns": {},
        "primary_development_score": "all 24 development cases",
        "secondary_development_score": "none",
    }
    assert manifest["history"] == [
        {
            "change": (
                "SYS-2028 deleted the SYS-2027 reviewed atomic-fact expectations "
                "by explicit user decision."
            ),
            "removed_filename": "expected_facts.json",
            "removed_files": 24,
            "facts_version": "geographic-facts-v10",
            "facts_prompt_hash": (
                "a1f18f4ac271b995c483b019dcaecd2235c3df0ac0e87ae1a7711cdd82574399"
            ),
            "screening_policy_version": "opportunity-screening-v2",
            "pre_removal_corpus_sha256": PRE_REMOVAL_CORPUS_HASH,
            "pre_removal_manifest_sha256": PRE_REMOVAL_MANIFEST_HASH,
        },
        {
            "change": (
                "Prompt examples replaced by synthetic examples written from the "
                "policy; no corpus case backs a prompt example, so all 24 "
                "development cases return to the primary score."
            ),
            "previous_example_cases": V1_PROMPT_EXAMPLE_CASES,
            "obligation_content_changed": False,
            "pre_change_corpus_sha256": PRE_SYNTHETIC_CORPUS_HASH,
            "pre_change_manifest_sha256": PRE_SYNTHETIC_MANIFEST_HASH,
        },
        {
            "change": (
                "Evidence obligations for KAK01 and OCE04 aligned with the v2 "
                "policy text: KAK01 accepts any one role remote or global "
                "permission statement, and OCE04 accepts the fully remote "
                "statement without its final period."
            ),
            "authorization": (
                "user decision recorded after the SYS-2028 v2 model comparison"
            ),
            "decision_date_utc": "2026-11-15",
            "changed_obligation_files": [
                "KAK01/expected_evidence.json",
                "OCE04/expected_evidence.json",
            ],
            "decisions_changed": False,
            "obligation_content_changed": True,
            "pre_change_corpus_sha256": PRE_OBLIGATION_REVISION_CORPUS_HASH,
            "pre_change_manifest_sha256": PRE_OBLIGATION_REVISION_MANIFEST_HASH,
        },
    ]
    assert set(V1_PROMPT_EXAMPLE_CASES.values()) == {
        example.pattern for example in PROMPT_EXAMPLES
    }
    assert manifest["lineage"] == {
        "source_evaluation_id": "opportunity-screening-v1",
        "source_status": "frozen",
        "source_cases_path": (
            "output/eligibility-replay/opportunity-screening-v1/cases.json"
        ),
        "source_manifest_path": (
            "output/eligibility-replay/opportunity-screening-v1/manifest.json"
        ),
        "v1_observed_hashes": {
            "cases_json_sha256": EXPECTED_V1_CASES_HASH,
            "manifest_json_sha256": EXPECTED_V1_MANIFEST_HASH,
        },
        "v1_declared_cases_sha256": EXPECTED_V1_CASES_HASH,
    }
    provenance = manifest["provenance"]
    assert provenance["eligibility_expectations_reviewed"] is True
    assert provenance["evidence_obligations_reviewed"] is True
    assert provenance["prompt_examples_authorized"] is True
    assert provenance["model_artifacts_included"] is False
    assert not any("fact_expectations" in key for key in provenance)


def test_case_paths_sources_and_obligation_boundary_are_exact() -> None:
    manifest = _payload(CORPUS / "manifest.json")
    source_hashes = manifest["source_hashes"]["per_case_content_sha256"]
    development = set(manifest["partitions"]["development"])
    exposed = set(manifest["partitions"]["exposed_regression"])
    directories = {path.name for path in CORPUS.iterdir() if path.is_dir()}
    evidence_case_ids = {
        path.parent.name for path in CORPUS.rglob("expected_evidence.json")
    }
    assert manifest["case_count"] == 32
    assert len(source_hashes) == 32
    assert development == EXPECTED_DEVELOPMENT
    assert exposed == EXPECTED_EXPOSED_REGRESSION
    assert directories == development | exposed == set(source_hashes)
    assert not list(CORPUS.rglob("expected_facts.json"))
    assert evidence_case_ids == development
    for case_id in exposed:
        assert {path.name for path in (CORPUS / case_id).iterdir()} == {
            "content.txt",
            "expected_eligibility.json",
        }
    for case_id in development:
        directory = CORPUS / case_id
        assert {path.name for path in directory.iterdir()} == set(DEVELOPMENT_FILES)
        content_bytes = (directory / "content.txt").read_bytes()
        source_hash = source_hashes[case_id]
        assert _sha256(content_bytes) == source_hash
        expectation = _payload(directory / "expected_eligibility.json")
        assert set(expectation) == EXPECTED_EXPECTATION_KEYS
        assert expectation["case_id"] == case_id
        assert expectation["reviewed"] is True
        assert expectation["source_content_sha256"] == source_hash
        assert expectation["partition"] == "development"
        assert set(expectation["provenance"]) == {
            "company",
            "title",
            "source_url",
            "captured_at_utc",
        }
        assert all(expectation["provenance"].values())
        assert expectation["lineage"]["source_evaluation_id"] == (
            "opportunity-screening-v1"
        )
        assert expectation["lineage"]["source_partition"] == "development"
        assert expectation["decision"] == expectation["lineage"]["source_decision"]
        normalized_content = _normalize_evidence(content_bytes.decode("utf-8"))
        assert expectation["must_cite"]
        for quote in expectation["must_cite"]:
            assert isinstance(quote, str) and quote.strip()
            assert _normalize_evidence(quote) in normalized_content


def test_development_obligations_are_grounded_and_consistent_with_labels() -> None:
    manifest = _payload(CORPUS / "manifest.json")
    source_hashes = manifest["source_hashes"]["per_case_content_sha256"]
    for case_id in sorted(EXPECTED_DEVELOPMENT):
        directory = CORPUS / case_id
        content = (directory / "content.txt").read_text(encoding="utf-8")
        eligibility = _payload(directory / "expected_eligibility.json")
        review = _payload(directory / "expected_evidence.json")
        _assert_no_forbidden_keys(review)
        assert set(review) == EXPECTED_EVIDENCE_KEYS
        assert review["schema_version"] == 1
        assert review["case_id"] == case_id
        assert review["reviewed"] is True
        assert review["review_status"] == "reviewed"
        assert review["partition"] == "development"
        assert review["source_content_sha256"] == source_hashes[case_id]
        assert review["decision"] == eligibility["decision"]
        assert review["missing_information"] is False
        # v2 examples are synthetic: no development case backs one.
        assert review["prompt_example"] is None
        obligations = review["obligations"]
        assert isinstance(obligations, list) and obligations
        identifiers: dict[str, str] = {}
        cited: set[str] = set()
        for index, obligation in enumerate(obligations, start=1):
            assert set(obligation) == EXPECTED_OBLIGATION_KEYS
            assert obligation["obligation_id"] == f"{case_id}-E{index:02d}"
            assert obligation["role"] in {"supporting", "conflicting", "either"}
            assert obligation["critical"] is True
            alternatives = obligation["excerpt_alternatives"]
            assert isinstance(alternatives, list) and alternatives
            for alternative in alternatives:
                assert isinstance(alternative, str) and alternative.strip()
                assert len(alternative) <= 300
                assert occurs_in(alternative, content)
            cited.update(alternatives)
            identifiers[obligation["obligation_id"]] = obligation["role"]
        # Every operator-reviewed must_cite quote survives as an alternative.
        assert set(eligibility["must_cite"]) <= cited
        pairs = review["conflict_pairs"]
        assert isinstance(pairs, list)
        for pair in pairs:
            assert len(pair) == 2 and pair[0] != pair[1]
            assert all(identifiers.get(item) == "either" for item in pair)
        if pairs:
            assert review["decision"] == "unknown"


def test_labels_partitions_and_single_v2_change_are_exact() -> None:
    manifest = _payload(CORPUS / "manifest.json")
    assert manifest["label_counts"] == EXPECTED_DECISIONS
    assert set(manifest["partitions"]) == {"development", "exposed_regression"}
    assert set(manifest["partitions"]["development"]) == EXPECTED_DEVELOPMENT
    assert set(manifest["partitions"]["exposed_regression"]) == (
        EXPECTED_EXPOSED_REGRESSION
    )
    assert manifest["v2_change"] == {
        "changed_case_ids": ["OCE03"],
        "decision_change": {
            "case_id": "OCE03",
            "from": "ineligible",
            "to": "unknown",
        },
        "reason": (
            "A foreign title or header is not restrictive without explicit "
            "residence, work-location, authorization, or on-site wording."
        ),
        "all_other_decisions_unchanged": True,
    }


def test_literal_hashes_and_corpus_hash_cover_every_fixture_file() -> None:
    manifest = _payload(CORPUS / "manifest.json")
    literal_hashes = manifest["literal_file_hashes"]["files"]
    expected_paths = {
        f"{case_id}/{filename}"
        for case_id in EXPECTED_DEVELOPMENT
        for filename in DEVELOPMENT_FILES
    } | {
        f"{case_id}/{filename}"
        for case_id in EXPECTED_EXPOSED_REGRESSION
        for filename in ("content.txt", "expected_eligibility.json")
    }
    actual_paths = {
        path.relative_to(CORPUS).as_posix()
        for path in CORPUS.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    assert len(literal_hashes) == 88
    assert set(literal_hashes) == expected_paths
    assert actual_paths == expected_paths
    for case_id, source_hash in manifest["source_hashes"][
        "per_case_content_sha256"
    ].items():
        assert literal_hashes[f"{case_id}/content.txt"] == source_hash
    development_hashes = _development_literal_hashes(EXPECTED_DEVELOPMENT)
    assert development_hashes == {
        path: literal_hashes[path]
        for path in expected_paths
        if path.split("/", 1)[0] in EXPECTED_DEVELOPMENT
    }
    assert manifest["corpus_hash"]["value"] == EXPECTED_CORPUS_HASH
    digest = hashlib.sha256()
    for relative in sorted(actual_paths):
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update((CORPUS / relative).read_bytes())
        digest.update(b"\0")
    assert digest.hexdigest() == EXPECTED_CORPUS_HASH


def test_corpus_has_no_symlinks_secrets_drafts_or_provider_artifacts() -> None:
    manifest = _payload(CORPUS / "manifest.json")
    paths = [CORPUS / relative for relative in manifest["literal_file_hashes"]["files"]]
    entries = list(CORPUS.rglob("*"))
    assert paths
    assert entries
    assert not CORPUS.is_symlink()
    assert not any(path.is_symlink() for path in entries)
    assert not any(
        path.is_dir()
        and path.name.casefold() in {"model_artifacts", "provider_artifacts"}
        for path in entries
    )
    for path in paths:
        assert path.name.casefold() not in FORBIDDEN_NAMES
    for path in [
        CORPUS / "manifest.json",
        *[
            CORPUS / case_id / filename
            for case_id in EXPECTED_DEVELOPMENT
            for filename in DEVELOPMENT_FILES
        ],
    ]:
        data = path.read_bytes()
        assert not any(pattern.search(data) for pattern in SECRET_PATTERNS)
        assert not FORBIDDEN_FIELD_PATTERN.search(data)
