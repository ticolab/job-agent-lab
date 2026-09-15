"""Unit tests for the ``vacantes batch`` subcommand (Phase 3).

Three surfaces, in increasing order of how much they touch:

- **Selection and flag parsing** are pure functions over a parsed
  namespace. Every namespace here is produced by the *real* parser via
  :func:`vacantes.cli.main.build_parser`, so these tests also pin the
  subcommand's wiring into the dispatcher — a flag that stops being
  registered fails here rather than in a manual run.
- **The schema preflight and the dry run** open a real SQLite file and
  assert what is *not* written.
- **The end-to-end run** registers a fake strategy over an entry in
  ``STRATEGIES`` and drives the whole command against a temporary
  database: no browser, no network, no LLM.

Async bodies go through :mod:`tests.unit.asyncio_harness` for the reason
that module documents.
"""

from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select

from tests.unit.asyncio_harness import run_async as _run
from tests.unit.asyncio_harness import track_engine
from vacantes.batch.policy import (
    DEFAULT_BROWSER_CONCURRENCY,
    DEFAULT_HTTP_CONCURRENCY,
    BatchPolicy,
)
from vacantes.cli import batch as cli_batch
from vacantes.cli.main import build_parser
from vacantes.domain.company import Company, StrategyName
from vacantes.extraction.base import STRATEGIES, RunContext, build_report
from vacantes.persistence import models
from vacantes.persistence.engine import create_engine, create_session_factory
from vacantes.persistence.timestamps import utc_now

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _args(*argv: str) -> argparse.Namespace:
    """Parse ``vacantes batch <argv>`` through the real dispatcher.

    Going through :func:`build_parser` rather than hand-building a
    namespace means these tests fail if the subcommand is unregistered
    or a flag is renamed, which is most of what could silently break.
    """
    return build_parser().parse_args(["batch", *argv])


def _company(
    name: str, alias: str, *, strategy: StrategyName = "greenhouse"
) -> Company:
    """A valid catalog entry with a predictable handle."""
    if strategy == "greenhouse":
        board = f"https://job-boards.greenhouse.io/{alias}"
    else:
        board = f"https://{alias}.example.com/careers"
    return Company(
        name=name,
        aliases=(alias,),
        job_board_url=board,
        sample_job_url=f"{board}/jobs/1",
        strategy=strategy,
    )


ALPHA = _company("Alpha Corp", "alpha")
BETA = _company("Beta Corp", "beta")
GAMMA = _company("Gamma Corp", "gamma", strategy="dom")


@pytest.fixture
def catalog(monkeypatch: pytest.MonkeyPatch) -> list[Company]:
    """Replace the corpus with three predictable entries.

    Patches both the name ``find_company`` closes over and the one the
    ``--all`` path reads, so handle resolution and whole-catalog
    selection agree.
    """
    companies = [ALPHA, BETA, GAMMA]
    monkeypatch.setattr("vacantes.catalog.COMPANIES", companies)
    monkeypatch.setattr("vacantes.cli.batch.COMPANIES", companies)
    return companies


class _FakeStrategy:
    """Returns a canned report, or raises for named slugs."""

    def __init__(
        self,
        jobs: dict[str, list[str]] | None = None,
        failing: set[str] | None = None,
    ) -> None:
        self.jobs = jobs or {}
        self.failing = failing or set()
        self.calls: list[str] = []

    async def extract(self, company: Company, ctx: RunContext) -> dict[str, Any]:
        self.calls.append(company.slug)
        if company.slug in self.failing:
            raise RuntimeError(f"{company.slug} is broken")
        urls = self.jobs.get(company.slug, [f"{company.job_board_url}/jobs/1"])
        return build_report(
            strategy=company.strategy,
            company_name=company.name,
            company_url=company.job_board_url,
            jobs=urls,
            elapsed=0.0,
            model=None,
            agent_steps=None,
            agent_completed=None,
            agent_had_errors=None,
            error=None,
            expected_jobs=company.expected_jobs,
        )


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> _FakeStrategy:
    """Register one fake over both strategies the fixtures use."""
    strategy = _FakeStrategy()
    monkeypatch.setitem(STRATEGIES, "greenhouse", strategy)
    monkeypatch.setitem(STRATEGIES, "dom", strategy)
    return strategy


async def _migrated(path: Path) -> Any:
    """Create the schema at *path* and return a tracked session factory."""
    engine = track_engine(create_engine(path))
    async with engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)
    return create_session_factory(engine)


async def _count(factory: Any, model: Any) -> int:
    """Count rows of *model*."""
    async with factory() as session:
        return int(
            (
                await session.execute(select(func.count()).select_from(model))
            ).scalar_one()
        )


# ---------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------


class TestSelection:
    """Handle resolution matches ``integrate`` and fails before any work."""

    def test_all_selects_the_whole_catalog(self, catalog: list[Company]) -> None:
        assert cli_batch.select_companies(_args("--all")) == catalog

    def test_a_repeated_company_flag_selects_several(
        self, catalog: list[Company]
    ) -> None:
        selected = cli_batch.select_companies(_args("-c", "alpha", "-c", "beta"))
        assert selected == [ALPHA, BETA]

    def test_handles_resolve_by_substring_like_integrate(
        self, catalog: list[Company]
    ) -> None:
        """Same precedence as ``integrate -c``: alias, acronym, substring."""
        assert cli_batch.select_companies(_args("-c", "gamma corp")) == [GAMMA]

    def test_a_handle_file_is_read_ignoring_blanks_and_comments(
        self, catalog: list[Company], tmp_path: Path
    ) -> None:
        listing = tmp_path / "companies.txt"
        listing.write_text(
            "# the two cheap ones\nalpha\n\n  beta  # trailing comment\n\n"
        )
        selected = cli_batch.select_companies(_args("--companies", str(listing)))
        assert selected == [ALPHA, BETA]

    def test_an_unreadable_handle_file_aborts(
        self, catalog: list[Company], tmp_path: Path
    ) -> None:
        with pytest.raises(SystemExit) as exc:
            cli_batch.select_companies(_args("--companies", str(tmp_path / "nope.txt")))
        assert exc.value.code == 2

    def test_every_unknown_handle_is_reported_at_once(
        self, catalog: list[Company], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """One run should surface every typo, not just the first.

        Failing on the first miss would make fixing a twenty-handle list
        a twenty-run exercise.
        """
        with pytest.raises(SystemExit) as exc:
            cli_batch.select_companies(
                _args("-c", "nosuch", "-c", "alpha", "-c", "alsono")
            )
        assert exc.value.code == 2
        message = capsys.readouterr().err
        assert "nosuch" in message
        assert "alsono" in message

    def test_exclude_drops_a_company(self, catalog: list[Company]) -> None:
        selected = cli_batch.select_companies(_args("--all", "--exclude", "beta"))
        assert selected == [ALPHA, GAMMA]

    def test_an_unknown_exclude_handle_aborts(self, catalog: list[Company]) -> None:
        with pytest.raises(SystemExit) as exc:
            cli_batch.select_companies(_args("--all", "--exclude", "nosuch"))
        assert exc.value.code == 2

    def test_duplicate_handles_collapse(self, catalog: list[Company]) -> None:
        """Two handles for one company must not run it twice.

        Running twice would have the second attempt skip on freshness
        the first one established, which reads as a bug rather than as
        de-duplication.
        """
        selected = cli_batch.select_companies(
            _args("-c", "alpha", "-c", "Alpha Corp", "-c", "alpha")
        )
        assert selected == [ALPHA]

    def test_excluding_everything_aborts(self, catalog: list[Company]) -> None:
        with pytest.raises(SystemExit) as exc:
            cli_batch.select_companies(_args("-c", "alpha", "--exclude", "alpha"))
        assert exc.value.code == 2

    def test_a_selection_is_required(self) -> None:
        """No bare ``vacantes batch`` that quietly means 250 boards."""
        with pytest.raises(SystemExit) as exc:
            _args()
        assert exc.value.code == 2

    def test_all_and_company_are_mutually_exclusive(self) -> None:
        with pytest.raises(SystemExit) as exc:
            _args("--all", "-c", "alpha")
        assert exc.value.code == 2


# ---------------------------------------------------------------------
# Policy flags
# ---------------------------------------------------------------------


class TestPolicyFlags:
    """The flags map onto ``BatchPolicy`` and reject unusable values."""

    def test_defaults_match_the_policy_defaults(self) -> None:
        """The help text's numbers and the dataclass's cannot drift."""
        assert cli_batch.build_policy(_args("--all")) == BatchPolicy()

    def test_flags_map_through(self) -> None:
        policy = cli_batch.build_policy(
            _args(
                "--all",
                "--browser-concurrency",
                "2",
                "--http-concurrency",
                "7",
                "--freshness-hours",
                "1.5",
                "--timeout-seconds",
                "30",
                "--force",
            )
        )
        assert policy.browser_concurrency == 2
        assert policy.http_concurrency == 7
        assert policy.freshness.total_seconds() == 5400
        assert policy.timeout.total_seconds() == 30
        assert policy.force is True

    @pytest.mark.parametrize("flag", ["--browser-concurrency", "--http-concurrency"])
    def test_a_zero_ceiling_is_rejected(self, flag: str) -> None:
        """A zero-permit semaphore would hang rather than refuse."""
        with pytest.raises(SystemExit) as exc:
            _args("--all", flag, "0")
        assert exc.value.code == 2

    def test_a_negative_freshness_window_is_rejected(self) -> None:
        with pytest.raises(SystemExit) as exc:
            _args("--all", "--freshness-hours", "-1")
        assert exc.value.code == 2

    def test_a_zero_freshness_window_is_allowed(self) -> None:
        """Zero is ``--force`` expressed as a window, not a mistake."""
        policy = cli_batch.build_policy(_args("--all", "--freshness-hours", "0"))
        assert policy.freshness.total_seconds() == 0


# ---------------------------------------------------------------------
# Schema preflight
# ---------------------------------------------------------------------


class TestSchemaPreflight:
    """An un-migrated database is diagnosed, not crashed into."""

    def test_a_database_with_no_schema_aborts_with_a_pointer(
        self,
        catalog: list[Company],
        fake: _FakeStrategy,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Connecting to a missing SQLite path happily creates an empty file.

        Without this check the first ever ``vacantes batch`` would die
        with an ``OperationalError`` from inside a repository, which
        says nothing about the migration the operator actually owes.
        """
        args = _args("-c", "alpha", "--database", str(tmp_path / "empty.db"))
        with pytest.raises(SystemExit) as exc:
            _run(cli_batch._run(args))
        assert exc.value.code == 2
        message = capsys.readouterr().err
        assert "alembic upgrade head" in message
        assert "company_runs" in message
        assert fake.calls == []


# ---------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------


class TestDryRun:
    """The plan reads the database and writes nothing at all."""

    def test_the_plan_lists_what_would_run_and_writes_nothing(
        self,
        catalog: list[Company],
        fake: _FakeStrategy,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        path = tmp_path / "plan.db"
        args = _args("--all", "--dry-run", "--database", str(path))

        async def scenario() -> tuple[int, int, int]:
            factory = await _migrated(path)
            await cli_batch._run(args)
            return (
                await _count(factory, models.CompanyRun),
                await _count(factory, models.Company),
                await _count(factory, models.JobUrl),
            )

        runs, projected, urls = _run(scenario())
        out = capsys.readouterr().out

        assert "dry run" in out
        assert "Would run (3)" in out
        # Every selected company, with its cost class and strategy.
        assert "alpha_corp" in out
        assert "browser" in out and "http" in out
        assert f"{DEFAULT_BROWSER_CONCURRENCY} browser" in out
        assert f"{DEFAULT_HTTP_CONCURRENCY} http" in out
        # "Touch nothing" is the contract: not even the catalog
        # projection the real run would refresh.
        assert (runs, projected, urls) == (0, 0, 0)
        assert fake.calls == []

    def test_a_fresh_company_is_listed_as_skipped(
        self,
        catalog: list[Company],
        fake: _FakeStrategy,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        path = tmp_path / "fresh-plan.db"
        args = _args("--all", "--dry-run", "--database", str(path))

        async def scenario() -> None:
            factory = await _migrated(path)
            now = utc_now()
            async with factory() as session, session.begin():
                session.add(
                    models.Company(
                        slug=ALPHA.slug,
                        name=ALPHA.name,
                        job_board_url=ALPHA.job_board_url,
                        strategy=ALPHA.strategy,
                        expected_jobs=None,
                        synced_at=now,
                    )
                )
            async with factory() as session, session.begin():
                session.add(
                    models.CompanyRun(
                        company_slug=ALPHA.slug,
                        batch_id="seeded",
                        status="success",
                        started_at=now,
                        finished_at=now,
                    )
                )
            await cli_batch._run(args)

        _run(scenario())
        out = capsys.readouterr().out

        assert "Would run (2)" in out
        assert "Skipped as fresh (1)" in out


# ---------------------------------------------------------------------
# The real run
# ---------------------------------------------------------------------


class TestRun:
    """End to end against a temporary database."""

    def test_a_run_persists_and_summarises(
        self,
        catalog: list[Company],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """One broken board is summarised, not fatal.

        Also pins the number the summary prints to the number the
        database holds, which is the §7 acceptance check.
        """
        strategy = _FakeStrategy(
            jobs={
                ALPHA.slug: [
                    f"{ALPHA.job_board_url}/jobs/1",
                    f"{ALPHA.job_board_url}/jobs/2",
                    # A duplicate the stored set collapses, so the
                    # printed total must be 2 rather than 3.
                    f"{ALPHA.job_board_url}/jobs/1",
                ]
            },
            failing={BETA.slug},
        )
        monkeypatch.setitem(STRATEGIES, "greenhouse", strategy)
        monkeypatch.setitem(STRATEGIES, "dom", strategy)

        path = tmp_path / "run.db"
        args = _args("--all", "--database", str(path))

        async def scenario() -> tuple[int, list[Any]]:
            factory = await _migrated(path)
            await cli_batch._run(args)
            stored = await _count(factory, models.JobUrl)
            async with factory() as session:
                rows = (
                    await session.execute(
                        select(
                            models.CompanyRun.company_slug,
                            models.CompanyRun.status,
                            models.CompanyRun.url_count,
                        ).order_by(models.CompanyRun.company_slug)
                    )
                ).all()
            return stored, list(rows)

        stored, rows = _run(scenario())
        out = capsys.readouterr().out

        assert "Success:   2" in out
        assert "Failed:    1" in out
        # Two from alpha (the duplicate collapsed) plus one from gamma.
        assert stored == 3
        assert f"Job URLs:  {stored} stored" in out
        assert f"{BETA.slug} is broken" in out

        by_slug = {slug: (status, count) for slug, status, count in rows}
        assert by_slug[ALPHA.slug] == ("success", 2)
        assert by_slug[BETA.slug][0] == "failed"

    def test_a_second_immediate_run_skips_everything_as_fresh(
        self, catalog: list[Company], fake: _FakeStrategy, tmp_path: Path
    ) -> None:
        """The §7 acceptance criterion for the freshness window."""
        path = tmp_path / "twice.db"

        async def scenario() -> list[str]:
            await _migrated(path)
            await cli_batch._run(_args("--all", "--database", str(path)))
            first_pass = list(fake.calls)
            fake.calls.clear()
            await cli_batch._run(_args("--all", "--database", str(path)))
            return [*first_pass, "|", *fake.calls]

        calls = _run(scenario())

        assert calls[-1] == "|", "the second run extracted something"
        assert len(calls) == 4

    def test_force_re_runs_a_fresh_company(
        self, catalog: list[Company], fake: _FakeStrategy, tmp_path: Path
    ) -> None:
        path = tmp_path / "forced.db"

        async def scenario() -> list[str]:
            await _migrated(path)
            await cli_batch._run(_args("-c", "alpha", "--database", str(path)))
            fake.calls.clear()
            await cli_batch._run(
                _args("-c", "alpha", "--force", "--database", str(path))
            )
            return list(fake.calls)

        assert _run(scenario()) == [ALPHA.slug]

    def test_an_abandoned_run_is_reaped_and_reported(
        self,
        catalog: list[Company],
        fake: _FakeStrategy,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Killing a batch mid-run and re-running resumes, per §7."""
        path = tmp_path / "reap.db"
        args = _args("-c", "alpha", "--database", str(path))

        async def scenario() -> None:
            factory = await _migrated(path)
            now = utc_now()
            async with factory() as session, session.begin():
                session.add(
                    models.Company(
                        slug=ALPHA.slug,
                        name=ALPHA.name,
                        job_board_url=ALPHA.job_board_url,
                        strategy=ALPHA.strategy,
                        expected_jobs=None,
                        synced_at=now,
                    )
                )
            async with factory() as session, session.begin():
                session.add(
                    models.CompanyRun(
                        company_slug=ALPHA.slug,
                        batch_id="killed",
                        status="in_progress",
                        started_at=now - timedelta(hours=3),
                    )
                )
            await cli_batch._run(args)

        _run(scenario())
        out = capsys.readouterr().out

        assert "Reaped:    1" in out
        assert "Success:   1" in out
        assert fake.calls == [ALPHA.slug]

    def test_json_output_writes_one_report_per_success(
        self, catalog: list[Company], fake: _FakeStrategy, tmp_path: Path
    ) -> None:
        """Routed through ``save_result``, so the shape matches integrate."""
        path = tmp_path / "json.db"
        reports = tmp_path / "reports"
        args = _args(
            "-c", "alpha", "--database", str(path), "--json-output", str(reports)
        )

        async def scenario() -> None:
            await _migrated(path)
            await cli_batch._run(args)

        _run(scenario())

        written = sorted(reports.glob("*.json"))
        assert len(written) == 1
        payload = json.loads(written[0].read_text())
        assert payload["company"] == ALPHA.name
        assert payload["metadata"]["strategy"] == "greenhouse"

    def test_no_json_is_written_without_the_flag(
        self, catalog: list[Company], fake: _FakeStrategy, tmp_path: Path
    ) -> None:
        path = tmp_path / "nojson.db"

        async def scenario() -> None:
            await _migrated(path)
            await cli_batch._run(_args("-c", "alpha", "--database", str(path)))

        _run(scenario())

        assert not list(tmp_path.glob("*.json"))
