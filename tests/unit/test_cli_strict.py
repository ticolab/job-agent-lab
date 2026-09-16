"""Unit tests for the ``--strict`` CLI gate.

Two surfaces:

- ``_has_non_match(results)`` is a pure predicate over the accumulated
  reports. Enumerated verdict rows plus the empty-list edge case.
- ``run_extraction(args)`` is the corpus loop that ``_has_non_match``
  guards. Two integration tests monkeypatch ``COMPANIES`` and
  ``get_strategy`` to control the verdict shape, then assert that:
  (a) every company still gets processed and saved regardless of
  strict mode (draining the loop before deciding the exit code is
  the contract), and (b) the process exits 1 iff ``--strict``
  is set *and* the run produced at least one non-match verdict.
"""

from __future__ import annotations

import argparse
import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest

import vacantes.cli.integrate as cli_mod
from vacantes.cli.integrate import _has_non_match, run_extraction
from vacantes.domain.company import Company


def _run(coro: Any) -> Any:
    """Drive a coroutine to completion in a worker thread's fresh loop.

    Cannot use ``asyncio.run(...)`` on the pytest main thread:
    ``pytest-playwright`` (loaded by the sibling snapshot suite)
    leaves an entry in ``asyncio.events._get_running_loop()`` on
    the test thread that survives even after its own tests finish.
    ``asyncio.run`` checks that thread-local and refuses to run
    when it is non-``None``.

    Running the coroutine in a fresh thread sidesteps the leak:
    each thread has its own asyncio thread-local state that starts
    as ``None``, so a brand-new loop can be created, driven to
    completion, and closed with no interference from Playwright's
    leftovers. Pattern mirrors ``tests/unit/test_greenhouse.py``.
    """
    result: dict[str, Any] = {}

    def _worker() -> None:
        loop = asyncio.new_event_loop()
        try:
            result["value"] = loop.run_until_complete(coro)
        except BaseException as exc:  # noqa: BLE001 — re-raised on main
            result["error"] = exc
        finally:
            loop.close()

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result["value"]


def _report(verdict: str, company: str = "Test", found: int = 0) -> dict[str, Any]:
    """Build the minimal report shape the strict gate reads.

    The ``(expected_jobs, verdict, total_jobs_found)`` triple is kept
    internally consistent so downstream consumers of the report (in
    particular :func:`vacantes.reporting.output._verdict_line`,
    which asserts non-``None`` ``expected_jobs`` on ``under``/``over``
    branches) accept the fixture as if it had come out of
    ``build_report``.
    """
    if verdict == "under":
        expected: int | None = found + 1  # expected > found
    elif verdict == "over":
        expected = max(found - 1, 0)  # expected < found
    elif verdict == "match":
        expected = found
    else:  # unverified
        expected = None

    # ``over`` requires found > expected: bump found when necessary.
    if verdict == "over" and found == 0:
        found = 1
        expected = 0

    return {
        "company": company,
        "url": "https://x.test/careers",
        "jobs": [],
        "metadata": {
            "strategy": "dom",
            "model": "m",
            "total_jobs_found": found,
            "extraction_time_seconds": 0.0,
            "agent_steps": 0,
            "agent_completed": True,
            "agent_had_errors": False,
            "error": None,
            "expected_jobs": expected,
            "verdict": verdict,
        },
    }


class TestHasNonMatch:
    """``_has_non_match`` is a pure verdict-set predicate."""

    def test_empty_returns_false(self) -> None:
        # No runs → nothing to fail on. Matches the pre-verdict no-op
        # semantics where a CLI invocation that matched zero
        # companies exited 0.
        assert _has_non_match([]) is False

    def test_all_match_returns_false(self) -> None:
        assert (
            _has_non_match([_report("match"), _report("match"), _report("match")])
            is False
        )

    @pytest.mark.parametrize("verdict", ["under", "over", "unverified"])
    def test_single_non_match_returns_true(self, verdict: str) -> None:
        # Each of the three non-match verdicts must trip the gate on
        # its own; ``unverified`` is critical because a never-counted
        # company must fail --strict.
        assert _has_non_match([_report("match"), _report(verdict)]) is True

    def test_all_unverified_returns_true(self) -> None:
        # Corpus-wide today (pre-backfill): every entry lands on
        # ``unverified`` and --strict should trip.
        assert _has_non_match([_report("unverified")] * 5) is True

    def test_mixed_verdicts_returns_true(self) -> None:
        assert (
            _has_non_match(
                [
                    _report("match"),
                    _report("under"),
                    _report("over"),
                    _report("unverified"),
                ]
            )
            is True
        )


class _FakeStrategy:
    """Minimal ``ExtractionStrategy`` stand-in for the loop test."""

    def __init__(self, verdict_by_company: dict[str, str]) -> None:
        self._by_company = verdict_by_company

    async def extract(self, company: Company, ctx: Any) -> dict[str, Any]:  # noqa: ARG002
        return _report(self._by_company[company.name], company=company.name)


def _make_company(name: str) -> Company:
    """Build a valid ``Company`` for the CLI loop under test."""
    return Company(
        name=name,
        aliases=(),
        job_board_url="https://example.test/careers",
        sample_job_url="https://example.test/careers/1",
    )


def _make_args(*, strict: bool, output_dir: Path) -> argparse.Namespace:
    """Build the argparse namespace ``run_extraction`` reads."""
    return argparse.Namespace(
        company=None,
        headed=False,
        model="gpt-4o-mini",
        max_steps=30,
        output_dir=str(output_dir),
        strict=strict,
    )


class TestRunExtractionStrictGate:
    """End-to-end: --strict decides the exit code post-loop."""

    def test_strict_off_never_exits_even_with_non_match(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Default corpus behaviour: unverified runs must not crash
        # the CLI. This is the byte-identical-to-pre-verdict case.
        companies = [_make_company("A"), _make_company("B")]
        monkeypatch.setattr(cli_mod, "COMPANIES", companies)
        fake = _FakeStrategy({"A": "match", "B": "unverified"})
        monkeypatch.setattr(cli_mod, "get_strategy", lambda _n: fake)

        # No SystemExit: strict is off, the gate is not consulted.
        _run(run_extraction(_make_args(strict=False, output_dir=tmp_path)))
        # Both companies must have produced JSON artefacts on disk —
        # draining the loop is the contract regardless of
        # strict mode.
        saved = list(tmp_path.glob("*.json"))
        assert len(saved) == 2

    def test_strict_on_all_match_exits_zero(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        companies = [_make_company("A"), _make_company("B")]
        monkeypatch.setattr(cli_mod, "COMPANIES", companies)
        fake = _FakeStrategy({"A": "match", "B": "match"})
        monkeypatch.setattr(cli_mod, "get_strategy", lambda _n: fake)

        # No SystemExit: everything matched, --strict is happy.
        _run(run_extraction(_make_args(strict=True, output_dir=tmp_path)))
        assert len(list(tmp_path.glob("*.json"))) == 2

    @pytest.mark.parametrize("bad_verdict", ["under", "over", "unverified"])
    def test_strict_on_single_non_match_exits_one_after_full_loop(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        bad_verdict: str,
    ) -> None:
        # The non-match company is placed *first* so the exit-code
        # decision must wait until after the second (matching)
        # company has been processed and saved. This locks the
        # "drain the loop, then decide" contract.
        companies = [_make_company("A"), _make_company("B")]
        monkeypatch.setattr(cli_mod, "COMPANIES", companies)
        fake = _FakeStrategy({"A": bad_verdict, "B": "match"})
        monkeypatch.setattr(cli_mod, "get_strategy", lambda _n: fake)

        with pytest.raises(SystemExit) as excinfo:
            _run(run_extraction(_make_args(strict=True, output_dir=tmp_path)))
        assert excinfo.value.code == 1
        # Every company saved before the exit gate fired.
        assert len(list(tmp_path.glob("*.json"))) == 2
