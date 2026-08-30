"""Unit tests for :mod:`job_agent_lab.reporting.output`.

Focused on the SYS-9 additions:

- :func:`_verdict_line` renders each of the four verdicts in the exact
  format the plan mandates (double space, em-dash, signed delta) and
  appends the ``(agent reported step errors)`` warning suffix only
  when ``agent_had_errors is True`` — not on ``None`` (API strategies)
  or ``False`` (successful DOM runs).
- :func:`print_summary` prints the verdict line for a full API-shape
  report where every agent field is ``None`` and the verdict is
  ``unverified`` — that combination is the most likely to break if
  the ``_fmt`` / verdict interaction ever regresses.

The pre-SYS-9 ``save_result`` / ``print_summary`` shape (``Company:``,
``URL:``, ``Strategy:``, ``Jobs:`` etc.) is exercised indirectly by
the golden runs in the snapshot harness and the CLI integration; the
tests here focus specifically on the verdict-line contract.
"""

from __future__ import annotations

from typing import Any

import pytest

from job_agent_lab.reporting.output import _verdict_line, print_summary


def _meta(
    *,
    verdict: str,
    expected_jobs: int | None,
    total_jobs_found: int,
    agent_had_errors: bool | None = False,
) -> dict[str, Any]:
    """Build the minimal metadata dict `_verdict_line` reads."""
    return {
        "verdict": verdict,
        "expected_jobs": expected_jobs,
        "total_jobs_found": total_jobs_found,
        "agent_had_errors": agent_had_errors,
    }


class TestVerdictLine:
    """Format contract for the ``Verdict:`` line."""

    def test_match_verdict_is_bare(self) -> None:
        # No delta, no annotation — a match is the clean case.
        line = _verdict_line(
            _meta(verdict="match", expected_jobs=3, total_jobs_found=3)
        )
        assert line == "Verdict:  match"

    def test_unverified_verdict_is_bare(self) -> None:
        # ``None`` expectation → no delta arithmetic possible.
        line = _verdict_line(
            _meta(verdict="unverified", expected_jobs=None, total_jobs_found=7)
        )
        assert line == "Verdict:  unverified"

    def test_over_verdict_renders_signed_positive_delta(self) -> None:
        # +36: the canonical "board grew unexpectedly" case from the
        # SYS-9 plan's example.
        line = _verdict_line(
            _meta(verdict="over", expected_jobs=3, total_jobs_found=39)
        )
        assert line == "Verdict:  over — found 39, expected 3 (+36)"

    def test_under_verdict_renders_signed_negative_delta(self) -> None:
        # -8: the "agent found fewer than the human counted" case.
        line = _verdict_line(
            _meta(verdict="under", expected_jobs=12, total_jobs_found=4)
        )
        assert line == "Verdict:  under — found 4, expected 12 (-8)"

    def test_delta_of_one_is_still_signed(self) -> None:
        # Guards against a silent switch from ``{delta:+d}`` to
        # ``{delta}`` — the ``+`` must be present even for a single-
        # unit overshoot.
        line = _verdict_line(_meta(verdict="over", expected_jobs=0, total_jobs_found=1))
        assert line == "Verdict:  over — found 1, expected 0 (+1)"

    def test_zero_over_zero_renders_match(self) -> None:
        # Case-C boards land here: legitimate zero on both sides. The
        # verdict is computed upstream, but the line must still render
        # as a bare ``match`` (no zero-delta suffix).
        line = _verdict_line(
            _meta(verdict="match", expected_jobs=0, total_jobs_found=0)
        )
        assert line == "Verdict:  match"

    @pytest.mark.parametrize(
        ("verdict", "expected", "found", "prefix"),
        [
            ("match", 5, 5, "Verdict:  match"),
            ("under", 5, 3, "Verdict:  under — found 3, expected 5 (-2)"),
            ("over", 5, 8, "Verdict:  over — found 8, expected 5 (+3)"),
            ("unverified", None, 8, "Verdict:  unverified"),
        ],
    )
    def test_annotation_appended_when_agent_had_errors_is_true(
        self, verdict: str, expected: int | None, found: int, prefix: str
    ) -> None:
        line = _verdict_line(
            _meta(
                verdict=verdict,
                expected_jobs=expected,
                total_jobs_found=found,
                agent_had_errors=True,
            )
        )
        assert line == f"{prefix} (agent reported step errors)"

    def test_no_annotation_when_agent_had_errors_is_false(self) -> None:
        # ``False`` is a successful DOM run — no annotation.
        line = _verdict_line(
            _meta(
                verdict="match",
                expected_jobs=3,
                total_jobs_found=3,
                agent_had_errors=False,
            )
        )
        assert line == "Verdict:  match"

    def test_no_annotation_when_agent_had_errors_is_none(self) -> None:
        # API strategies emit ``None`` — the annotation is DOM-agent-
        # specific and must not fire on ``None``. The ``is True``
        # check in ``_verdict_line`` guarantees this by construction.
        line = _verdict_line(
            _meta(
                verdict="match",
                expected_jobs=3,
                total_jobs_found=3,
                agent_had_errors=None,
            )
        )
        assert line == "Verdict:  match"


class TestPrintSummaryVerdictLine:
    """End-to-end: verdict line appears in ``print_summary`` output."""

    def _full_report(
        self, verdict: str, expected: int | None, found: int
    ) -> dict[str, Any]:
        # Full API-shape report: every agent field is ``None`` so the
        # ``n/a`` rendering path is exercised. Used to catch any
        # regression where the verdict line's presence depends on
        # agent-field truthiness.
        return {
            "company": "Zscaler",
            "url": "https://job-boards.greenhouse.io/zscaler",
            "jobs": ["https://job-boards.greenhouse.io/zscaler/jobs/1"] * found,
            "metadata": {
                "strategy": "greenhouse",
                "model": None,
                "total_jobs_found": found,
                "extraction_time_seconds": 0.5,
                "agent_steps": None,
                "agent_completed": None,
                "agent_had_errors": None,
                "error": None,
                "expected_jobs": expected,
                "verdict": verdict,
            },
        }

    def test_prints_verdict_line_for_api_unverified_report(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        print_summary(self._full_report("unverified", None, 2))
        out = capsys.readouterr().out
        assert "Verdict:  unverified" in out
        # ``n/a`` rendering for agent-only fields must be preserved.
        assert "Steps:    n/a" in out
        assert "Done:     n/a" in out
        assert "Errors:   n/a" in out

    def test_prints_signed_over_delta_for_api_report(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        print_summary(self._full_report("over", 3, 5))
        out = capsys.readouterr().out
        assert "Verdict:  over — found 5, expected 3 (+2)" in out

    def test_verdict_line_precedes_error_when_error_present(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Ordering matters: SYS-9 plan puts the verdict line between
        # ``Errors:`` and the ``Error:`` string block. Lock it here
        # so a future refactor doesn't accidentally swap them.
        report = self._full_report("under", 5, 0)
        report["metadata"]["error"] = "HTTP 503"
        print_summary(report)
        out = capsys.readouterr().out
        verdict_idx = out.index("Verdict:  under")
        error_idx = out.index("Error:    HTTP 503")
        assert verdict_idx < error_idx
