"""Pin the frozen blind corpus: contents, labels, hashes, and separation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from job_eligibility import PROMPT_EXAMPLES, occurs_in

ROOT = Path(__file__).resolve().parents[2]
BLIND = ROOT / "tests/fixtures/postings/opportunity-screening-blind-v1"
DEVELOPMENT = ROOT / "tests/fixtures/postings/opportunity-screening-v2"
EXPECTED_CORPUS_HASH = (
    "1ef10ae5ede8a8359ef265278e981b6e250d9db8d919f1e3c8a3dd5b1b2ec148"
)


def _json(path: Path) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return payload


def test_manifest_counts_hashes_and_corpus_hash_are_exact() -> None:
    manifest = _json(BLIND / "manifest.json")
    assert manifest["corpus_id"] == "opportunity-screening-blind-v1"
    assert manifest["partition"] == "blind"
    assert manifest["case_count"] == 51
    assert manifest["label_counts"] == {"eligible": 23, "ineligible": 11, "unknown": 17}
    assert min(manifest["label_counts"].values()) >= 10
    assert manifest["prompt_examples"]["source"] == "synthetic"
    files = {
        p.relative_to(BLIND).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in BLIND.rglob("*")
        if p.is_file() and p.name != "manifest.json"
    }
    assert files == manifest["literal_file_hashes"]["files"]
    digest = hashlib.sha256()
    for rel in sorted(files):
        digest.update(rel.encode())
        digest.update(b"\0")
        digest.update((BLIND / rel).read_bytes())
        digest.update(b"\0")
    assert (
        digest.hexdigest() == manifest["corpus_hash"]["value"] == EXPECTED_CORPUS_HASH
    )


def test_every_case_is_reviewed_grounded_and_consistent() -> None:
    manifest = _json(BLIND / "manifest.json")
    cases = sorted(p for p in BLIND.iterdir() if p.is_dir())
    assert len(cases) == 51
    for case in cases:
        content = (case / "content.txt").read_text(encoding="utf-8")
        label = _json(case / "expected_eligibility.json")
        evidence = _json(case / "expected_evidence.json")
        digest = hashlib.sha256(content.encode()).hexdigest()
        assert manifest["source_hashes"]["per_case_content_sha256"][case.name] == digest
        assert label["case_id"] == evidence["case_id"] == case.name
        assert label["partition"] == evidence["partition"] == "blind"
        assert label["reviewed"] is True and evidence["reviewed"] is True
        assert evidence["review_status"] == "reviewed"
        assert (
            label["source_content_sha256"]
            == evidence["source_content_sha256"]
            == digest
        )
        assert label["decision"] == evidence["decision"]
        assert evidence["prompt_example"] is None
        assert evidence["missing_information"] == (not evidence["obligations"])
        for obligation in evidence["obligations"]:
            assert obligation["critical"] is True
            for alternative in obligation["excerpt_alternatives"]:
                assert occurs_in(alternative, content)
        if evidence["conflict_pairs"]:
            assert label["decision"] == "unknown"
        assert set(label) >= {"must_cite", "rationale", "provenance"}


def test_blind_postings_are_disjoint_from_development_and_prompt_examples() -> None:
    development = {
        p.read_text(encoding="utf-8") for p in DEVELOPMENT.glob("*/content.txt")
    }
    development_urls = {
        _json(p)["provenance"]["source_url"]
        for p in DEVELOPMENT.glob("*/expected_eligibility.json")
    }
    development_companies = {
        _json(p)["provenance"]["company"]
        for p in DEVELOPMENT.glob("*/expected_eligibility.json")
    }
    example_lines = {
        line.strip()
        for example in PROMPT_EXAMPLES
        for line in example.posting.splitlines()
        if len(line.split()) >= 3
    }
    for case in sorted(BLIND.glob("BL*")):
        content = (case / "content.txt").read_text(encoding="utf-8")
        provenance = _json(case / "expected_eligibility.json")["provenance"]
        assert content not in development
        assert provenance["source_url"] not in development_urls
        assert provenance["company"] not in development_companies
        assert not example_lines & {line.strip() for line in content.splitlines()}
