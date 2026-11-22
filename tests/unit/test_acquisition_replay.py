from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from job_pipeline.content.model import AcquiredContent, AcquisitionFailure
from tests.unit.acquisition_harness import (
    CAPTURE_ROOT,
    DESCRIPTION,
    REQUIRED,
    SYNTHETIC_DATE,
    SYNTHETIC_URL,
    AutomatedCapture,
    approved_captures,
    block_live_access,
    digest,
    freeze_selection_date,
    read_record,
    record,
    replay_selection,
    select_capture,
    synthetic_capture,
    synthetic_html,
    synthetic_review,
    validate_automated,
    validate_capture,
    validate_review,
    write_record,
)


@pytest.fixture(autouse=True)
def offline_only(monkeypatch: pytest.MonkeyPatch) -> None:
    block_live_access(monkeypatch)


def test_repository_approved_capture_selection_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for path in approved_captures(include_automated=True):
        with monkeypatch.context() as clock:
            replay_selection(validate_capture(path), clock)


@pytest.fixture
def gap_capture() -> AutomatedCapture:
    return validate_automated(
        CAPTURE_ROOT
        / "growth_acceleration_partners"
        / "senior_databricks_apps_full_stack_engineer"
    )


def test_gap_replay_contains_complete_browser_reference(
    gap_capture: AutomatedCapture, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = replay_selection(gap_capture, monkeypatch)
    assert result.method == "jsonld"
    assert len(gap_capture.required_excerpts) == 65
    assert "Latam, USA" in result.text
    assert "Hybrid" in result.text


@pytest.mark.parametrize("mutation", ["passage", "location", "heading", "title"])
def test_automated_replay_rejects_missing_full_reference_passage(
    gap_capture: AutomatedCapture,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    result = replay_selection(gap_capture, monkeypatch)
    excerpt = {
        "passage": (
            "Experience with Apache ECharts or similar data visualization libraries."
        ),
        "location": "Latam, USA",
        "heading": "Professional Experience",
        "title": "Senior Databricks Apps Full-Stack Engineer",
    }[mutation]
    altered = replace(result, text=result.text.replace(excerpt, ""))
    monkeypatch.setattr(
        "tests.unit.acquisition_harness.select_capture", lambda **kwargs: altered
    )
    with pytest.raises(AssertionError, match="required reference passage missing"):
        replay_selection(gap_capture, monkeypatch)


def test_automated_replay_allows_formatting_and_section_reordering(
    gap_capture: AutomatedCapture, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = replay_selection(gap_capture, monkeypatch)
    reordered = "\u2003\n".join(reversed(result.text.splitlines()))
    reordered = reordered.replace("Full-time", "Ｆｕｌｌ-time")
    assert reordered != result.text
    altered = replace(result, text=reordered)
    monkeypatch.setattr(
        "tests.unit.acquisition_harness.select_capture", lambda **kwargs: altered
    )
    assert replay_selection(gap_capture, monkeypatch) is altered


@pytest.mark.parametrize(
    "method",
    [
        "jsonld",
        "main_content",
        "rendered_jsonld",
        "rendered_main",
        "rendered_frame_jsonld",
        "rendered_frame_main",
    ],
)
def test_synthetic_parent_and_selected_frame_selection_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    path = synthetic_capture(tmp_path, monkeypatch, method=method)
    synthetic_review(path)
    capture = validate_review(path)
    result = replay_selection(capture, monkeypatch)
    assert result.method == method
    assert REQUIRED in result.text
    assert bool(result.frame_html) == method.startswith("rendered_frame_")
    assert result.retrieved_at.isoformat() == SYNTHETIC_DATE


@pytest.mark.parametrize("framed", [False, True])
@pytest.mark.parametrize(
    ("instant", "closed"),
    [
        ("2026-06-14T23:59:59+00:00", False),
        ("2026-06-15T12:00:00+00:00", False),
        ("2026-06-16T00:00:00+00:00", True),
    ],
)
def test_selection_replay_expiry_before_on_after_recorded_date(
    monkeypatch: pytest.MonkeyPatch, framed: bool, instant: str, closed: bool
) -> None:
    html = synthetic_html(valid_through="2026-06-15")
    retrieved_at = freeze_selection_date(monkeypatch, instant)
    result = select_capture(
        html="<html><body>Shell</body></html>" if framed else html,
        frame_html=html if framed else "",
        method="rendered_frame_jsonld" if framed else "jsonld",
        url=SYNTHETIC_URL,
        status=200,
        retrieved_at=retrieved_at,
    )
    if closed:
        assert isinstance(result, AcquisitionFailure)
        assert result.category == "closed"
    else:
        assert isinstance(result, AcquiredContent)
        assert REQUIRED in result.text


def test_replay_uses_recorded_date_not_current_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = synthetic_capture(tmp_path, monkeypatch, valid_through="2026-06-15")
    synthetic_review(path)
    freeze_selection_date(monkeypatch, "2099-01-01T00:00:00+00:00")
    result = replay_selection(validate_review(path), monkeypatch)
    assert result.retrieved_at.isoformat() == SYNTHETIC_DATE


@pytest.mark.parametrize(
    ("retrieved_at", "captured_at"),
    [
        ("2026-06-15T23:59:59+00:00", "2026-06-16T00:00:01+00:00"),
        (SYNTHETIC_DATE, "2026-06-15T11:59:59+00:00"),
        ("2026-06-15T12:00:00", SYNTHETIC_DATE),
        (SYNTHETIC_DATE, "2026-06-15T12:00:01"),
    ],
)
def test_replay_refuses_uncertain_selection_date(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    retrieved_at: str,
    captured_at: str,
) -> None:
    path = synthetic_capture(tmp_path, monkeypatch)
    synthetic_review(path)
    metadata_path = path / "meta.json"
    metadata = read_record(metadata_path)
    metadata.update({"retrieved_at": retrieved_at, "captured_at": captured_at})
    write_record(metadata_path, metadata)
    review = read_record(path / "review.json")
    record(review["file_digests"], "digests")["meta.json"] = digest(metadata_path)
    write_record(path / "review.json", review)
    with pytest.raises(AssertionError, match="selection date uncertain"):
        replay_selection(validate_review(path), monkeypatch)


def test_recorded_clock_is_converted_to_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = freeze_selection_date(monkeypatch, "2026-06-15T23:30:00-06:00")
    assert recorded.isoformat() == "2026-06-16T05:30:00+00:00"
    result = select_capture(
        html=synthetic_html(valid_through="2026-06-15"),
        frame_html="",
        method="jsonld",
        url=SYNTHETIC_URL,
        status=200,
        retrieved_at=recorded,
    )
    assert isinstance(result, AcquisitionFailure)
    assert result.category == "closed"


@pytest.mark.parametrize("field", ["title", "method"])
def test_selection_checks_metadata_expectations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    path = synthetic_capture(tmp_path, monkeypatch)
    synthetic_review(path)
    metadata_path = path / "meta.json"
    metadata = read_record(metadata_path)
    metadata[field] = "Wrong synthetic title" if field == "title" else "main_content"
    write_record(metadata_path, metadata)
    review = read_record(path / "review.json")
    record(review["file_digests"], "digests")["meta.json"] = digest(metadata_path)
    write_record(path / "review.json", review)
    with pytest.raises(AssertionError, match=field):
        replay_selection(validate_review(path), monkeypatch)


def test_selection_replay_reconstructs_recorded_http_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = synthetic_capture(tmp_path, monkeypatch)
    synthetic_review(path)
    metadata_path = path / "meta.json"
    metadata = read_record(metadata_path)
    metadata["http_status"] = 201
    write_record(metadata_path, metadata)
    review = read_record(path / "review.json")
    record(review["file_digests"], "digests")["meta.json"] = digest(metadata_path)
    write_record(path / "review.json", review)
    assert replay_selection(validate_review(path), monkeypatch).http_status == 201


@pytest.mark.parametrize("framed", [False, True])
def test_selection_detects_html_text_divergence_even_with_rebound_file_digests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, framed: bool
) -> None:
    path = synthetic_capture(
        tmp_path, monkeypatch, method="rendered_frame_jsonld" if framed else "jsonld"
    )
    synthetic_review(path)
    filename = "frame.html" if framed else "page.html"
    html_path = path / filename
    html_path.write_text(
        html_path.read_text(encoding="utf-8").replace(
            REQUIRED, "Altered synthetic duty."
        ),
        encoding="utf-8",
    )
    metadata_path = path / "meta.json"
    metadata = read_record(metadata_path)
    record(metadata["file_digests"], "digests")[filename] = digest(html_path)
    write_record(metadata_path, metadata)
    review = read_record(path / "review.json")
    review_digests = record(review["file_digests"], "digests")
    review_digests[filename] = digest(html_path)
    review_digests["meta.json"] = digest(metadata_path)
    write_record(path / "review.json", review)
    with pytest.raises(AssertionError, match="selection text"):
        replay_selection(validate_review(path), monkeypatch)


def test_rendered_parent_precedence_is_production_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = select_capture(
        html=synthetic_html(),
        frame_html=synthetic_html().replace(
            "instrument specialist", "frame specialist"
        ),
        method="rendered_frame_jsonld",
        url=SYNTHETIC_URL,
        status=200,
        retrieved_at=freeze_selection_date(monkeypatch, SYNTHETIC_DATE),
    )
    assert isinstance(result, AcquiredContent)
    assert result.method == "rendered_jsonld"
    assert result.title == "Synthetic instrument specialist"
    assert result.frame_html == ""


def test_selected_frame_must_qualify_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = select_capture(
        html="<html><body>Short parent</body></html>",
        frame_html=synthetic_html().replace(DESCRIPTION, "Too short"),
        method="rendered_frame_jsonld",
        url=SYNTHETIC_URL,
        status=200,
        retrieved_at=freeze_selection_date(monkeypatch, SYNTHETIC_DATE),
    )
    assert result is None
