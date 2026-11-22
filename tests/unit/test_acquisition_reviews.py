from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from job_pipeline.content.model import hash_content
from job_pipeline.runner.capture import _string_values, secret_findings
from tests.unit.acquisition_harness import (
    CAPTURE_ROOT,
    COMPLETENESS_CRITERION,
    DESCRIPTION,
    FORBIDDEN,
    REQUIRED,
    AutomatedCapture,
    approved_captures,
    block_live_access,
    digest,
    read_record,
    record,
    reference_excerpts,
    replay_selection,
    synthetic_capture,
    synthetic_review,
    validate_automated,
    validate_capture,
    validate_review,
    write_record,
)


@pytest.fixture(autouse=True)
def offline_only(monkeypatch: pytest.MonkeyPatch) -> None:
    block_live_access(monkeypatch)


@pytest.fixture
def synthetic_approved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = synthetic_capture(tmp_path, monkeypatch)
    synthetic_review(path)
    return path


def test_repository_approved_capture_reviews() -> None:
    for path in approved_captures(include_automated=True):
        validate_capture(path)


GAP_CAPTURE = (
    CAPTURE_ROOT
    / "growth_acceleration_partners"
    / "senior_databricks_apps_full_stack_engineer"
)


@pytest.fixture
def automated_capture(tmp_path: Path) -> Path:
    destination = tmp_path / "growth_acceleration_partners" / GAP_CAPTURE.name
    shutil.copytree(GAP_CAPTURE, destination)
    return destination


@pytest.mark.parametrize("source_absent", [False, True])
def test_synthetic_automated_location_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_absent: bool
) -> None:
    description = "Synthetic instrument specialist\n" + DESCRIPTION.replace(
        "Location: Synthetic Valley.", "\nLocation: Synthetic Valley.\n"
    )
    monkeypatch.setattr("tests.unit.acquisition_harness.DESCRIPTION", description)
    path = synthetic_capture(tmp_path, monkeypatch, location_present=not source_absent)
    metadata = read_record(path / "meta.json")
    description = description.replace("Location: Synthetic Valley.", "")
    snapshot: dict[str, object] = {
        "title": metadata["title"],
        "description": description,
        "details_text": "",
        "source_location_absent": source_absent,
    }
    if not source_absent:
        snapshot["location_text"] = "Location: Synthetic Valley."
    reference: dict[str, object] = {
        "snapshot": snapshot,
        "selected_url": metadata["source_url"],
        "resolved_url": metadata["resolved_url"],
    }
    write_record(path / "reference.json", reference)
    validation: dict[str, object] = {
        "status": "success",
        "validation_type": "automated_browser_comparison",
        "human_approval": False,
        "completeness_criterion": COMPLETENESS_CRITERION,
        "checks": dict.fromkeys(
            ("title", "description", "location", "capture_integrity"), True
        ),
        "omissions": [],
        "source_location_absent": source_absent,
        "validated_at": metadata["captured_at"],
        "selected_url": metadata["source_url"],
        "acquisition_context": metadata["context"],
        "content_hash": metadata["content_hash"],
        "required_excerpts": list(reference_excerpts(reference)),
        "file_digests": {
            name: digest(path / name)
            for name in ("page.html", "content.txt", "meta.json", "reference.json")
        },
    }
    write_record(path / "validation.json", validation)
    capture = validate_automated(path)
    replay_selection(capture, monkeypatch)
    assert capture.validation["checks"] == dict.fromkeys(
        ("title", "description", "location", "capture_integrity"), True
    )
    if source_absent:
        assert "Synthetic Valley" not in capture.text
    else:
        content = path / "content.txt"
        text = content.read_text(encoding="utf-8").replace(
            "Location: Synthetic Valley.", ""
        )
        content.write_text(text, encoding="utf-8")
        metadata["content_hash"] = hash_content(text)
        record(metadata["file_digests"], "digests")["content.txt"] = digest(content)
        write_record(path / "meta.json", metadata)
        validation["content_hash"] = hash_content(text)
        digests = record(validation["file_digests"], "digests")
        digests["content.txt"] = digest(content)
        digests["meta.json"] = digest(path / "meta.json")
        write_record(path / "validation.json", validation)
        with pytest.raises(AssertionError, match="required reference passage missing"):
            validate_automated(path)


@pytest.mark.parametrize("absent", [None, "true", 0, 1])
def test_reference_location_absence_requires_boolean(absent: object) -> None:
    reference = read_record(GAP_CAPTURE / "reference.json")
    record(reference["snapshot"], "snapshot")["source_location_absent"] = absent
    with pytest.raises(AssertionError, match="reference source_location_absent"):
        reference_excerpts(reference)


def test_absent_reference_cannot_also_supply_location() -> None:
    reference = read_record(GAP_CAPTURE / "reference.json")
    snapshot = record(reference["snapshot"], "snapshot")
    snapshot["source_location_absent"] = True
    snapshot["location_text"] = "Latam, USA"
    with pytest.raises(AssertionError, match="absent reference location conflicts"):
        reference_excerpts(reference)


def test_automated_capture_is_not_human_approval(automated_capture: Path) -> None:
    root = automated_capture.parent.parent
    assert approved_captures(root) == ()
    assert approved_captures(root, include_automated=True) == (automated_capture,)
    capture = validate_capture(automated_capture)
    assert isinstance(capture, AutomatedCapture)
    assert capture.validation["human_approval"] is False
    assert not (automated_capture / "review.json").exists()
    with pytest.raises(FileNotFoundError):
        validate_review(automated_capture)


@pytest.mark.parametrize("filename", ["validation.json", "reference.json"])
def test_automated_discovery_requires_both_records(
    automated_capture: Path, filename: str
) -> None:
    (automated_capture / filename).unlink()
    with pytest.raises(AssertionError, match=f"missing {filename}"):
        approved_captures(automated_capture.parent.parent)


@pytest.mark.parametrize("value", ["[]", "null", "{}", "{broken"])
def test_malformed_automated_validation_rejected(
    automated_capture: Path, value: str
) -> None:
    (automated_capture / "validation.json").write_text(value, encoding="utf-8")
    with pytest.raises((AssertionError, ValueError)):
        approved_captures(automated_capture.parent.parent)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "failure"),
        ("validation_type", "human_review"),
        ("human_approval", True),
        ("human_approval", 0),
        ("reviewer", "fabricated"),
        ("completeness_criterion", "any keyword"),
        ("checks", {"title": True}),
        ("omissions", ["missing passage"]),
        ("source_location_absent", True),
        ("required_excerpts", ["Summary"]),
        ("content_hash", "0" * 64),
        ("selected_url", "https://synthetic.invalid/wrong"),
        ("acquisition_context", "http-only"),
        ("validated_at", "2026-11-22T01:12:22"),
    ],
)
def test_forged_automated_validation_rejected(
    automated_capture: Path, field: str, value: object
) -> None:
    path = automated_capture / "validation.json"
    validation = read_record(path)
    validation[field] = value
    write_record(path, validation)
    with pytest.raises(AssertionError):
        validate_automated(automated_capture)


@pytest.mark.parametrize(
    "filename", ["page.html", "content.txt", "meta.json", "reference.json"]
)
def test_automated_validation_binds_actual_bytes(
    automated_capture: Path, filename: str
) -> None:
    path = automated_capture / filename
    path.write_bytes(path.read_bytes() + b" \n")
    with pytest.raises(AssertionError, match="digest"):
        validate_automated(automated_capture)


@pytest.mark.parametrize("mutation", ["missing", "forged", "extra"])
def test_automated_digest_map_is_strict(automated_capture: Path, mutation: str) -> None:
    path = automated_capture / "validation.json"
    validation = read_record(path)
    digests = record(validation["file_digests"], "digests")
    if mutation == "missing":
        digests.pop("meta.json")
    elif mutation == "forged":
        digests["meta.json"] = "0" * 64
    else:
        digests["browser-state.json"] = "0" * 64
    write_record(path, validation)
    with pytest.raises(AssertionError, match="digest"):
        validate_automated(automated_capture)


def test_pending_review_never_becomes_automated_human_approval(
    automated_capture: Path,
) -> None:
    review: dict[str, object] = {
        "status": "pending",
        "reviewer": None,
        "review_date": None,
    }
    write_record(automated_capture / "review.json", review)
    validate_automated(automated_capture)
    assert approved_captures(automated_capture.parent.parent) == ()
    review["status"] = "approved"
    write_record(automated_capture / "review.json", review)
    with pytest.raises(AssertionError, match="conflicting human review"):
        validate_automated(automated_capture)


def test_tracked_automated_bundle_has_no_secret_findings() -> None:
    assert {path.name for path in GAP_CAPTURE.iterdir()} == {
        "page.html",
        "content.txt",
        "meta.json",
        "reference.json",
        "validation.json",
    }
    for path in GAP_CAPTURE.iterdir():
        assert not secret_findings(path.read_text(encoding="utf-8")), path.name
        if path.suffix == ".json":
            assert not secret_findings(*_string_values(read_record(path))), path.name


@pytest.mark.parametrize(
    "excerpt",
    [
        "Latam, USA",
        "Professional Experience",
        "Experience with Apache ECharts or similar data visualization libraries.",
    ],
)
def test_automated_completeness_rejects_rebound_incomplete_capture(
    automated_capture: Path, excerpt: str
) -> None:
    path = automated_capture / "content.txt"
    text = path.read_text(encoding="utf-8").replace(excerpt, "")
    path.write_text(text, encoding="utf-8")
    metadata = read_record(automated_capture / "meta.json")
    metadata["content_hash"] = hash_content(text)
    record(metadata["file_digests"], "digests")["content.txt"] = digest(path)
    write_record(automated_capture / "meta.json", metadata)
    validation = read_record(automated_capture / "validation.json")
    validation["content_hash"] = hash_content(text)
    digests = record(validation["file_digests"], "digests")
    digests["content.txt"] = digest(path)
    digests["meta.json"] = digest(automated_capture / "meta.json")
    write_record(automated_capture / "validation.json", validation)
    with pytest.raises(AssertionError, match="required reference passage missing"):
        validate_automated(automated_capture)


def test_discovery_rejects_deleted_review(synthetic_approved: Path) -> None:
    root = synthetic_approved.parent.parent
    assert approved_captures(root) == (synthetic_approved,)
    (synthetic_approved / "review.json").unlink()
    with pytest.raises(AssertionError, match="missing review.json"):
        approved_captures(root)


@pytest.mark.parametrize("status", ["pending", "rejected", "blocked"])
def test_discovery_requires_review_before_status_filtering(
    synthetic_approved: Path, status: str
) -> None:
    review_path = synthetic_approved / "review.json"
    review = read_record(review_path)
    review["status"] = status
    write_record(review_path, review)
    review_path.unlink()
    with pytest.raises(AssertionError, match="missing review.json"):
        approved_captures(synthetic_approved.parent.parent)


@pytest.mark.parametrize(
    "filename", ["page.html", "frame.html", "content.txt", "review.json"]
)
def test_discovery_detects_orphan_capture_artifacts(
    tmp_path: Path, filename: str
) -> None:
    path = tmp_path / "synthetic" / "orphan" / filename
    path.parent.mkdir(parents=True)
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(AssertionError, match="missing meta.json"):
        approved_captures(tmp_path)


@pytest.mark.parametrize(
    "relative", ["page.html", "slug/meta.json", "slug/case/extra/review.json"]
)
def test_discovery_rejects_malformed_bundle_depth(
    tmp_path: Path, relative: str
) -> None:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(AssertionError, match="must be at <slug>/<case>"):
        approved_captures(tmp_path)


def test_discovery_rejects_directory_as_capture_file(tmp_path: Path) -> None:
    (tmp_path / "synthetic" / "case" / "review.json").mkdir(parents=True)
    with pytest.raises(AssertionError, match="artifact must be a file"):
        approved_captures(tmp_path)


@pytest.mark.parametrize("status", ["failure", None, "invalid"])
def test_discovery_rejects_failure_or_invalid_capture_status(
    tmp_path: Path, status: object
) -> None:
    path = tmp_path / "synthetic" / "failure"
    path.mkdir(parents=True)
    write_record(path / "meta.json", {"status": status})
    with pytest.raises(AssertionError, match="only successful candidate bundles"):
        approved_captures(tmp_path)


@pytest.mark.parametrize("value", ["[]", "null", '"invalid"'])
def test_discovery_rejects_malformed_metadata_object(
    tmp_path: Path, value: str
) -> None:
    path = tmp_path / "synthetic" / "malformed"
    path.mkdir(parents=True)
    (path / "meta.json").write_text(value, encoding="utf-8")
    with pytest.raises(AssertionError, match="meta.json: expected object"):
        approved_captures(tmp_path)


@pytest.mark.parametrize("filename", ["page.html", "content.txt"])
def test_discovery_checks_candidate_completeness_before_filtering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filename: str
) -> None:
    path = synthetic_capture(tmp_path, monkeypatch)
    (path / filename).unlink()
    with pytest.raises(AssertionError, match=f"missing {filename}"):
        approved_captures(tmp_path)


def test_discovery_accepts_empty_or_absent_corpus(tmp_path: Path) -> None:
    assert approved_captures(tmp_path) == ()
    assert approved_captures(tmp_path / "absent") == ()
    (tmp_path / "empty-slug" / "empty-case").mkdir(parents=True)
    (tmp_path / "unrelated.txt").write_text("not a capture", encoding="utf-8")
    assert approved_captures(tmp_path) == ()


def test_synthetic_approved_review_validates(synthetic_approved: Path) -> None:
    capture = validate_review(synthetic_approved)
    assert REQUIRED in capture.text
    assert capture.review["reviewer"] == "SYNTHETIC TEST INPUT, NOT HUMAN APPROVAL"


@pytest.mark.parametrize("status", ["pending", "rejected", "blocked"])
def test_unapproved_captures_never_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    path = synthetic_capture(tmp_path, monkeypatch)
    review = read_record(path / "review.json")
    review["status"] = status
    write_record(path / "review.json", review)
    assert approved_captures(tmp_path) == ()
    with pytest.raises(AssertionError, match="not approved"):
        validate_review(path)
    synthetic_review(path)
    assert approved_captures(tmp_path) == (path,)


def test_published_template_has_no_manufactured_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = synthetic_capture(tmp_path, monkeypatch)
    review = read_record(path / "review.json")
    assert review["status"] == "pending"
    assert review["reviewer"] is None
    assert review["review_date"] is None
    assert review["checks"] == {"title": None, "description": None, "location": None}
    assert review["location_absent_at_source"] is None


@pytest.mark.parametrize("field", ["reviewer", "review_date"])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_approval_requires_identity_and_date(
    synthetic_approved: Path, field: str, value: object
) -> None:
    review = read_record(synthetic_approved / "review.json")
    review[field] = value
    write_record(synthetic_approved / "review.json", review)
    with pytest.raises(AssertionError, match=field):
        validate_review(synthetic_approved)


def test_invalid_review_date_rejected(synthetic_approved: Path) -> None:
    review = read_record(synthetic_approved / "review.json")
    review["review_date"] = "not-a-date"
    write_record(synthetic_approved / "review.json", review)
    with pytest.raises(ValueError):
        validate_review(synthetic_approved)


@pytest.mark.parametrize("field", ["title", "description", "location"])
@pytest.mark.parametrize("value", [None, "true", 1, False])
def test_review_checks_require_explicit_booleans(
    synthetic_approved: Path, field: str, value: object
) -> None:
    review = read_record(synthetic_approved / "review.json")
    checks = record(review["checks"], "checks")
    checks[field] = value
    write_record(synthetic_approved / "review.json", review)
    with pytest.raises(AssertionError, match=field):
        validate_review(synthetic_approved)


@pytest.mark.parametrize("value", [None, "false", 0])
def test_location_absence_requires_boolean(
    synthetic_approved: Path, value: object
) -> None:
    review = read_record(synthetic_approved / "review.json")
    review["location_absent_at_source"] = value
    write_record(synthetic_approved / "review.json", review)
    with pytest.raises(AssertionError, match="location_absent_at_source"):
        validate_review(synthetic_approved)


def test_explicit_source_location_absence_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    synthetic_approved = synthetic_capture(
        tmp_path, monkeypatch, location_present=False
    )
    synthetic_review(synthetic_approved)
    assert "Location: Synthetic Valley." not in (
        synthetic_approved / "content.txt"
    ).read_text(encoding="utf-8")
    review = read_record(synthetic_approved / "review.json")
    checks = record(review["checks"], "checks")
    checks["location"] = False
    review["location_absent_at_source"] = True
    review["required_excerpts"] = [REQUIRED]
    review["omissions"] = ["Synthetic source has no location; test input only."]
    write_record(synthetic_approved / "review.json", review)
    assert (
        validate_review(synthetic_approved).review["location_absent_at_source"] is True
    )


@pytest.mark.parametrize(
    "field", ["omissions", "required_excerpts", "forbidden_excerpts"]
)
@pytest.mark.parametrize("value", [None, "not a list", [False], [""]])
def test_review_lists_are_validated(
    synthetic_approved: Path, field: str, value: object
) -> None:
    review = read_record(synthetic_approved / "review.json")
    review[field] = value
    write_record(synthetic_approved / "review.json", review)
    with pytest.raises(AssertionError, match=field):
        validate_review(synthetic_approved)


def test_approved_review_requires_meaningful_excerpt(synthetic_approved: Path) -> None:
    review = read_record(synthetic_approved / "review.json")
    review["required_excerpts"] = []
    write_record(synthetic_approved / "review.json", review)
    with pytest.raises(AssertionError, match="required_excerpts"):
        validate_review(synthetic_approved)


@pytest.mark.parametrize("mutation", ["delete-required", "insert-forbidden"])
def test_excerpt_validation_detects_content_regressions(
    synthetic_approved: Path, mutation: str
) -> None:
    path = synthetic_approved / "content.txt"
    text = path.read_text(encoding="utf-8")
    if mutation == "delete-required":
        path.write_text(text.replace(REQUIRED, ""), encoding="utf-8")
        message = "required excerpt missing"
    else:
        path.write_text(text + FORBIDDEN, encoding="utf-8")
        message = "forbidden excerpt present"
    with pytest.raises(AssertionError, match=message):
        validate_review(synthetic_approved)


@pytest.mark.parametrize("filename", ["page.html", "content.txt", "meta.json"])
def test_captured_byte_changes_break_review_binding(
    synthetic_approved: Path, filename: str
) -> None:
    path = synthetic_approved / filename
    path.write_bytes(path.read_bytes() + b" \n")
    with pytest.raises(AssertionError, match="digest"):
        validate_review(synthetic_approved)


@pytest.mark.parametrize("filename", ["page.html", "content.txt", "meta.json"])
def test_required_capture_files_cannot_disappear(
    synthetic_approved: Path, filename: str
) -> None:
    (synthetic_approved / filename).unlink()
    with pytest.raises(FileNotFoundError):
        validate_review(synthetic_approved)


def test_changed_content_fails_production_hash(synthetic_approved: Path) -> None:
    path = synthetic_approved / "content.txt"
    path.write_text(
        path.read_text(encoding="utf-8") + "Different synthetic responsibility.\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="metadata content_hash"):
        validate_review(synthetic_approved)


def test_review_file_digest_cannot_disagree_with_metadata(
    synthetic_approved: Path,
) -> None:
    path = synthetic_approved / "review.json"
    review = read_record(path)
    record(review["file_digests"], "digests")["content.txt"] = "0" * 64
    write_record(path, review)
    with pytest.raises(AssertionError, match="review digest: content.txt"):
        validate_review(synthetic_approved)


@pytest.mark.parametrize("filename", ["review.json", "meta.json"])
def test_approved_content_hash_must_match(
    synthetic_approved: Path, filename: str
) -> None:
    path = synthetic_approved / filename
    value = read_record(path)
    value["content_hash"] = "0" * 64
    write_record(path, value)
    with pytest.raises(AssertionError, match="content_hash"):
        validate_review(synthetic_approved)


def test_metadata_and_review_digest_links_are_independent(
    synthetic_approved: Path,
) -> None:
    page = synthetic_approved / "page.html"
    page.write_bytes(page.read_bytes() + b"\n")
    metadata = read_record(synthetic_approved / "meta.json")
    record(metadata["file_digests"], "digests")["page.html"] = digest(page)
    write_record(synthetic_approved / "meta.json", metadata)
    with pytest.raises(AssertionError, match="review digest: page.html"):
        validate_review(synthetic_approved)


@pytest.mark.parametrize("filename", ["review.json", "meta.json"])
def test_digest_maps_require_all_and_only_capture_files(
    synthetic_approved: Path, filename: str
) -> None:
    path = synthetic_approved / filename
    value = read_record(path)
    record(value["file_digests"], "digests").pop("page.html")
    write_record(path, value)
    with pytest.raises(AssertionError, match="file_digests members"):
        validate_review(synthetic_approved)


def test_failed_capture_cannot_be_approved(synthetic_approved: Path) -> None:
    path = synthetic_approved / "meta.json"
    metadata = read_record(path)
    metadata["status"] = "failure"
    write_record(path, metadata)
    with pytest.raises(AssertionError, match="not successful"):
        validate_review(synthetic_approved)


@pytest.mark.parametrize("mutation", ["changed", "missing", "untracked"])
def test_frame_file_integrity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    path = synthetic_capture(tmp_path, monkeypatch, method="rendered_frame_jsonld")
    synthetic_review(path)
    validate_review(path)
    frame = path / "frame.html"
    if mutation == "changed":
        frame.write_bytes(frame.read_bytes() + b"\n")
    elif mutation == "missing":
        frame.unlink()
    else:
        metadata_path = path / "meta.json"
        metadata = read_record(metadata_path)
        metadata["frame"] = False
        write_record(metadata_path, metadata)
    with pytest.raises(AssertionError, match="frame"):
        validate_review(path)
