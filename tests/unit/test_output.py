"""Unit tests for :mod:`job_discovery.reporting.output`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from job_discovery.reporting.output import print_summary, save_result


def _report(
    *, jobs: list[str] | None = None, error: str | None = None
) -> dict[str, Any]:
    urls = jobs or []
    return {
        "company": "Zscaler",
        "url": "https://job-boards.greenhouse.io/zscaler",
        "jobs": urls,
        "metadata": {
            "strategy": "greenhouse",
            "model": None,
            "total_jobs_found": len(urls),
            "extraction_time_seconds": 0.5,
            "agent_steps": None,
            "agent_completed": None,
            "agent_had_errors": None,
            "error": error,
        },
    }


class TestPrintSummary:
    def test_prints_remaining_summary_fields(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        url = "https://job-boards.greenhouse.io/zscaler/jobs/1"
        print_summary(_report(jobs=[url]))
        out = capsys.readouterr().out

        assert "Company:  Zscaler" in out
        assert "Strategy: greenhouse" in out
        assert "Jobs:     1" in out
        assert "Steps:    n/a" in out
        assert "Done:     n/a" in out
        assert "Errors:   n/a" in out
        assert url in out
        assert "Verdict:" not in out
        assert "expected" not in out.lower()

    def test_prints_error_without_jobs(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        print_summary(_report(error="HTTP 503"))
        out = capsys.readouterr().out

        assert "Jobs:     0" in out
        assert "Error:    HTTP 503" in out
        assert "Job URLs" not in out

    def test_prints_states_when_present(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        report = _report()
        report["metadata"]["states_visited"] = 3
        print_summary(report)

        assert "States:   3" in capsys.readouterr().out


class TestSaveResult:
    def test_writes_the_remaining_report_shape(self, tmp_path: Path) -> None:
        report = _report(jobs=["https://example.test/jobs/1"])
        path = save_result(report, tmp_path)

        assert path.parent == tmp_path
        assert path.name.startswith("zscaler_")
        assert json.loads(path.read_text()) == report
