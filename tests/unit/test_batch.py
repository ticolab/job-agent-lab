"""Unit tests for the batch core (Phase 2).

No browser and no network: every test registers a fake strategy over an
entry in :data:`~vacantes.extraction.base.STRATEGIES` and runs the real
scheduler, worker, and repositories against a real SQLite file under
``tmp_path``. The fake is what makes the concurrency assertions possible
— it records its own high-water mark per class, so the ceiling is
observed rather than assumed.

Async scenarios are driven through
:mod:`tests.unit.asyncio_harness`; see that module for why the main
thread's event loop is unusable in a full-suite run.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.unit.asyncio_harness import run_async as _run
from tests.unit.asyncio_harness import track_engine
from vacantes.batch.policy import BatchPolicy, concurrency_class
from vacantes.batch.scheduler import BatchResult, new_batch_id, run_batch
from vacantes.batch.worker import CompanyOutcome, run_company
from vacantes.domain.company import Company, CoveoConfig
from vacantes.domain.region import COSTA_RICA_LATAM
from vacantes.extraction.base import STRATEGIES, RunContext, build_report
from vacantes.persistence import jobs_repo, models, runs_repo
from vacantes.persistence.engine import create_engine, create_session_factory
from vacantes.persistence.timestamps import utc_now

# ---------------------------------------------------------------------
# Fixtures and fakes
# ---------------------------------------------------------------------

CTX = RunContext(
    model="fake-model", headless=True, max_steps=1, region=COSTA_RICA_LATAM
)

# Every strategy registered today, with the concurrency class it must be
# assigned. Set equality against `STRATEGIES` is asserted below, so an
# eighth adapter fails this suite until someone decides whether it costs
# a browser — which is the whole point of classifying by cost.
DECLARED_CLASSES: dict[str, str] = {
    "dom": "browser",
    "greenhouse": "http",
    "phenom": "http",
    "talentbrew": "http",
    "coveo": "http",
    "peopleforce": "http",
    "bamboohr": "http",
}


def _browser_company(index: int) -> Company:
    """A ``dom`` company — always ``browser`` class."""
    host = f"https://browser{index}.example.com"
    return Company(
        name=f"Browser {index}",
        job_board_url=f"{host}/careers",
        sample_job_url=f"{host}/careers/jobs/1",
        strategy="dom",
    )


def _http_company(index: int) -> Company:
    """A ``greenhouse`` company — always ``http`` class.

    Greenhouse is the cheapest valid API entry to construct: it is gated
    by a canonical host rather than by a per-tenant config object.
    """
    board = f"https://job-boards.greenhouse.io/http{index}"
    return Company(
        name=f"Http {index}",
        job_board_url=board,
        sample_job_url=f"{board}/jobs/1",
        strategy="greenhouse",
    )


def _report_for(company: Company, jobs: list[str]) -> dict[str, Any]:
    """Build an honest report through the real report author."""
    return build_report(
        strategy=company.strategy,
        company_name=company.name,
        company_url=company.job_board_url,
        jobs=jobs,
        elapsed=0.0,
        model=None,
        agent_steps=None,
        agent_completed=None,
        agent_had_errors=None,
        error=None,
        expected_jobs=company.expected_jobs,
    )


class _FakeStrategy:
    """A strategy that records concurrency and does what it is told.

    ``handler`` maps a company slug to the coroutine function invoked for
    it, so one instance can succeed for one company and raise for
    another in the same batch. The default handler returns a one-job
    report.

    ``high_water`` is the peak number of *simultaneously executing*
    extractions, which is what the semaphore ceiling has to bound. It is
    tracked per concurrency class because the two buckets are
    independent.
    """

    name = "fake"

    def __init__(self) -> None:
        self.handlers: dict[str, Callable[[Company], Awaitable[dict[str, Any]]]] = {}
        self.calls: list[str] = []
        self.live: dict[str, int] = {"browser": 0, "http": 0}
        self.high_water: dict[str, int] = {"browser": 0, "http": 0}
        # How many simultaneous extractions a ceiling test expects. Zero
        # disables the gate, which is what every other test wants.
        self.expect: dict[str, int] = {"browser": 0, "http": 0}
        self._reached: dict[str, asyncio.Event] = {
            "browser": asyncio.Event(),
            "http": asyncio.Event(),
        }

    async def _await_ceiling(self, cls: str) -> None:
        """Block until ``expect[cls]`` extractions are live at once.

        Makes the ceiling assertion deterministic rather than a race
        against however long the workers spend in the database. Without
        it the high-water mark depends on scheduling luck and lands
        below the ceiling on a fast machine.

        The timeout is the failure mode that matters: if the semaphore
        admits fewer than ``expect`` at a time, nobody ever reaches the
        target, and the wait raises instead of hanging the suite.
        """
        target = self.expect[cls]
        if not target:
            return
        if self.live[cls] >= target:
            self._reached[cls].set()
        await asyncio.wait_for(self._reached[cls].wait(), timeout=5)

    async def _default(self, company: Company) -> dict[str, Any]:
        await asyncio.sleep(0.01)
        return _report_for(company, [f"{company.job_board_url}/jobs/1"])

    async def extract(self, company: Company, ctx: RunContext) -> dict[str, Any]:
        cls = concurrency_class(company)
        self.calls.append(company.slug)
        self.live[cls] += 1
        self.high_water[cls] = max(self.high_water[cls], self.live[cls])
        try:
            await self._await_ceiling(cls)
            handler = self.handlers.get(company.slug, self._default)
            return await handler(company)
        finally:
            self.live[cls] -= 1


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> _FakeStrategy:
    """Register one fake over both strategies the fixtures use."""
    strategy = _FakeStrategy()
    monkeypatch.setitem(STRATEGIES, "dom", strategy)
    monkeypatch.setitem(STRATEGIES, "greenhouse", strategy)
    return strategy


async def _fresh_database(path: Path) -> async_sessionmaker[AsyncSession]:
    """Create the schema at *path* and return a session factory."""
    engine = track_engine(create_engine(path))
    async with engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)
    return create_session_factory(engine)


async def _seed_run(
    factory: async_sessionmaker[AsyncSession],
    *,
    company: Company,
    status: str,
    started_at: Any = None,
    finished_at: Any = None,
) -> int:
    """Insert a ``company_runs`` row directly, bypassing the repository.

    Tests that need a *pre-existing* run — a fresh success, an abandoned
    ``in_progress`` — are describing a state the database was already in
    when the batch started, not an action the batch took.
    """
    now = utc_now()
    async with factory() as session, session.begin():
        session.add(
            models.Company(
                slug=company.slug,
                name=company.name,
                job_board_url=company.job_board_url,
                strategy=company.strategy,
                expected_jobs=company.expected_jobs,
                synced_at=now,
            )
        )
    async with factory() as session, session.begin():
        run = models.CompanyRun(
            company_slug=company.slug,
            batch_id="seeded",
            status=status,
            started_at=started_at or now,
            finished_at=finished_at,
        )
        session.add(run)
        await session.flush()
        return int(run.id)


# ---------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------


class TestConcurrencyClass:
    """Cost classification, including the one case a name split misses."""

    def test_every_registered_strategy_has_a_declared_class(self) -> None:
        """A new adapter must be assigned a class before it can ship.

        Mirrors the existing ``StrategyName``-versus-registry test. Left
        undeclared, a browser-backed adapter would default into the
        twenty-wide HTTP bucket and exhaust memory on the first full
        run.
        """
        assert set(STRATEGIES) == set(DECLARED_CLASSES)

    def test_dom_is_browser_class(self) -> None:
        assert concurrency_class(_browser_company(1)) == "browser"

    def test_plain_api_strategy_is_http_class(self) -> None:
        assert concurrency_class(_http_company(1)) == "http"

    def test_coveo_with_a_browser_token_is_browser_class(self) -> None:
        """The case classification by strategy name gets wrong.

        This company opens a real browser to read the JWT its own page
        minted into ``sessionStorage`` (C19), so it costs a Chromium
        context despite being an API strategy.
        """
        company = Company(
            name="Coveo Browser Token",
            job_board_url="https://coveo.example.com/careers",
            sample_job_url="https://coveo.example.com/careers/jobs/1",
            strategy="coveo",
            coveo=CoveoConfig(
                organization_id="org",
                search_hub="hub",
                browser_token_key="searchToken_en_us",
            ),
        )
        assert concurrency_class(company) == "browser"

    def test_coveo_with_an_http_token_is_http_class(self) -> None:
        """The same strategy on the HTTP mint path stays cheap."""
        company = Company(
            name="Coveo Http Token",
            job_board_url="https://coveo2.example.com/careers",
            sample_job_url="https://coveo2.example.com/careers/jobs/1",
            strategy="coveo",
            coveo=CoveoConfig(
                organization_id="org",
                search_hub="hub",
                token_url="https://coveo2.example.com/token",
            ),
        )
        assert concurrency_class(company) == "http"


# ---------------------------------------------------------------------
# Concurrency ceilings
# ---------------------------------------------------------------------


class TestConcurrencyCeilings:
    """The semaphores actually bound simultaneous execution."""

    def test_each_class_is_capped_at_its_own_ceiling(
        self, tmp_path: Path, fake: _FakeStrategy
    ) -> None:
        """Neither bucket exceeds its limit, and both reach it.

        Asserting the high-water mark *equals* the ceiling matters as
        much as asserting it never exceeds it: a scheduler that
        accidentally serialised everything would satisfy ``<=`` while
        making the batch useless.
        """
        companies = [_browser_company(i) for i in range(8)]
        companies += [_http_company(i) for i in range(12)]
        policy = BatchPolicy(browser_concurrency=2, http_concurrency=5)
        fake.expect = {"browser": 2, "http": 5}

        async def scenario() -> BatchResult:
            factory = await _fresh_database(tmp_path / "ceilings.db")
            return await run_batch(
                companies, CTX, session_factory=factory, policy=policy
            )

        result = _run(scenario())

        assert result.succeeded == 20
        assert fake.high_water["browser"] == 2
        assert fake.high_water["http"] == 5

    def test_the_two_buckets_are_independent(
        self, tmp_path: Path, fake: _FakeStrategy
    ) -> None:
        """A saturated browser bucket does not throttle HTTP work.

        One shared semaphore would make the cheap bucket wait behind the
        expensive one, which is the reason there are two.
        """
        companies = [_browser_company(i) for i in range(4)]
        companies += [_http_company(i) for i in range(4)]
        policy = BatchPolicy(browser_concurrency=1, http_concurrency=4)
        fake.expect = {"browser": 1, "http": 4}

        async def scenario() -> BatchResult:
            factory = await _fresh_database(tmp_path / "independent.db")
            return await run_batch(
                companies, CTX, session_factory=factory, policy=policy
            )

        _run(scenario())

        assert fake.high_water["browser"] == 1
        assert fake.high_water["http"] == 4


# ---------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------


class TestFailureIsolation:
    """One broken board does not abort the batch."""

    def test_one_company_raising_leaves_the_rest_unaffected(
        self, tmp_path: Path, fake: _FakeStrategy
    ) -> None:
        companies = [_http_company(i) for i in range(3)]
        broken = companies[1]

        async def explode(company: Company) -> dict[str, Any]:
            raise RuntimeError("board redesigned")

        fake.handlers[broken.slug] = explode

        async def scenario() -> tuple[BatchResult, list[Any]]:
            factory = await _fresh_database(tmp_path / "isolation.db")
            result = await run_batch(companies, CTX, session_factory=factory)
            async with factory() as session:
                rows = (
                    await session.execute(
                        select(
                            models.CompanyRun.company_slug,
                            models.CompanyRun.status,
                            models.CompanyRun.error_message,
                        ).order_by(models.CompanyRun.id)
                    )
                ).all()
            return result, list(rows)

        result, rows = _run(scenario())

        assert (result.succeeded, result.failed, result.skipped) == (2, 1, 0)
        statuses = {slug: status for slug, status, _ in rows}
        assert statuses[broken.slug] == "failed"
        assert statuses[companies[0].slug] == "success"
        assert statuses[companies[2].slug] == "success"

        errors = {slug: error for slug, _, error in rows}
        assert "board redesigned" in errors[broken.slug]

    def test_the_outcome_carries_the_same_message_that_was_recorded(
        self, tmp_path: Path, fake: _FakeStrategy
    ) -> None:
        """The operator's two views of a failure cannot disagree."""
        company = _http_company(0)

        async def explode(_: Company) -> dict[str, Any]:
            raise ValueError("nope")

        fake.handlers[company.slug] = explode

        async def scenario() -> tuple[CompanyOutcome, str | None]:
            factory = await _fresh_database(tmp_path / "message.db")
            result = await run_batch([company], CTX, session_factory=factory)
            async with factory() as session:
                recorded = (
                    await session.execute(select(models.CompanyRun.error_message))
                ).scalar_one()
            return result.outcomes[0], recorded

        outcome, recorded = _run(scenario())

        assert outcome.status == "failed"
        assert outcome.error == recorded

    def test_a_persistence_failure_after_extraction_is_isolated_too(
        self, tmp_path: Path, fake: _FakeStrategy, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A database error is that company's failure, not the batch's.

        Before the persistence calls were inside the boundary, an
        exception from the URL write escaped into ``asyncio.gather``,
        which raises on the first failure while leaving every other task
        running detached — their outcomes lost and their browser slots
        held until process exit. Here the victim's write fails, the other
        two companies must still complete, and the victim's run row must
        be marked ``failed`` with the database's own message, since
        ``fail_run`` itself still works in this scenario.
        """
        companies = [_http_company(i) for i in range(3)]
        victim = companies[1]
        real_replace = jobs_repo.replace_company_urls

        async def locked_for_victim(
            session: AsyncSession,
            *,
            company_slug: str,
            urls: Sequence[str],
            captured_at: datetime,
        ) -> int:
            if company_slug == victim.slug:
                raise OperationalError(
                    "INSERT INTO job_urls", {}, Exception("database is locked")
                )
            return await real_replace(
                session, company_slug=company_slug, urls=urls, captured_at=captured_at
            )

        monkeypatch.setattr(jobs_repo, "replace_company_urls", locked_for_victim)

        async def scenario() -> tuple[BatchResult, dict[str, Any]]:
            factory = await _fresh_database(tmp_path / "db-failure.db")
            result = await run_batch(companies, CTX, session_factory=factory)
            async with factory() as session:
                rows = (
                    await session.execute(
                        select(
                            models.CompanyRun.company_slug,
                            models.CompanyRun.status,
                            models.CompanyRun.error_message,
                        )
                    )
                ).all()
            return result, {slug: (status, error) for slug, status, error in rows}

        result, rows = _run(scenario())

        assert (result.succeeded, result.failed, result.skipped) == (2, 1, 0)
        victim_outcome = result.outcomes[1]
        assert victim_outcome.status == "failed"
        assert victim_outcome.error is not None
        assert "database is locked" in victim_outcome.error
        # The row and the outcome are the operator's two views of one
        # failure; they must agree, and the other companies must have
        # finished normally.
        assert rows[victim.slug] == ("failed", victim_outcome.error)
        assert rows[companies[0].slug][0] == "success"
        assert rows[companies[2].slug][0] == "success"

    def test_a_failure_before_the_run_row_exists_is_isolated_too(
        self, tmp_path: Path, fake: _FakeStrategy, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Zone 1: no run row yet, so the outcome alone carries the error.

        ``start_run`` raising for one company must not abort the batch,
        and — because the row insert is what failed — that company must
        have no ``company_runs`` row at all, while the others complete.
        """
        companies = [_http_company(i) for i in range(3)]
        victim = companies[0]
        real_start = runs_repo.start_run

        async def refuse_victim(
            session: AsyncSession, *, company_slug: str, batch_id: str
        ) -> int:
            if company_slug == victim.slug:
                raise OperationalError(
                    "INSERT INTO company_runs", {}, Exception("disk I/O error")
                )
            return await real_start(
                session, company_slug=company_slug, batch_id=batch_id
            )

        monkeypatch.setattr(runs_repo, "start_run", refuse_victim)

        async def scenario() -> tuple[BatchResult, list[str]]:
            factory = await _fresh_database(tmp_path / "pre-run-failure.db")
            result = await run_batch(companies, CTX, session_factory=factory)
            async with factory() as session:
                slugs = (
                    (await session.execute(select(models.CompanyRun.company_slug)))
                    .scalars()
                    .all()
                )
            return result, list(slugs)

        result, slugs_with_rows = _run(scenario())

        assert (result.succeeded, result.failed, result.skipped) == (2, 1, 0)
        assert result.outcomes[0].status == "failed"
        assert result.outcomes[0].error is not None
        assert "disk I/O error" in result.outcomes[0].error
        assert victim.slug not in slugs_with_rows
        assert set(slugs_with_rows) == {companies[1].slug, companies[2].slug}

    def test_a_company_over_its_timeout_is_recorded_failed(
        self, tmp_path: Path, fake: _FakeStrategy
    ) -> None:
        """A hung board releases its slot instead of stalling the batch."""
        company = _http_company(0)

        async def hang(_: Company) -> dict[str, Any]:
            await asyncio.sleep(30)
            raise AssertionError("should have been cancelled")

        fake.handlers[company.slug] = hang
        policy = BatchPolicy(timeout=timedelta(milliseconds=50))

        async def scenario() -> tuple[CompanyOutcome, str | None]:
            factory = await _fresh_database(tmp_path / "timeout.db")
            result = await run_batch(
                [company], CTX, session_factory=factory, policy=policy
            )
            async with factory() as session:
                recorded = (
                    await session.execute(select(models.CompanyRun.error_message))
                ).scalar_one()
            return result.outcomes[0], recorded

        outcome, recorded = _run(scenario())

        assert outcome.status == "failed"
        assert outcome.error == "timeout after 0.05s"
        assert recorded == "timeout after 0.05s"

    def test_zero_jobs_is_a_success_not_a_failure(
        self, tmp_path: Path, fake: _FakeStrategy
    ) -> None:
        """An empty board is an unhappy verdict on a successful run.

        Marking it failed would put honestly-empty boards into the retry
        path forever and hide genuine breakage among them.
        """
        company = _http_company(0)

        async def empty(c: Company) -> dict[str, Any]:
            return _report_for(c, [])

        fake.handlers[company.slug] = empty

        async def scenario() -> tuple[BatchResult, Any]:
            factory = await _fresh_database(tmp_path / "empty.db")
            result = await run_batch([company], CTX, session_factory=factory)
            async with factory() as session:
                row = (
                    await session.execute(
                        select(models.CompanyRun.status, models.CompanyRun.url_count)
                    )
                ).one()
            return result, row

        result, row = _run(scenario())

        assert result.succeeded == 1
        assert row.status == "success"
        assert row.url_count == 0


# ---------------------------------------------------------------------
# Skip policy
# ---------------------------------------------------------------------


class TestSkipPolicy:
    """Freshness keys on successes, which is what makes retry work."""

    def test_a_fresh_success_is_skipped_without_extracting(
        self, tmp_path: Path, fake: _FakeStrategy
    ) -> None:
        company = _http_company(0)

        async def scenario() -> BatchResult:
            factory = await _fresh_database(tmp_path / "fresh.db")
            await _seed_run(
                factory, company=company, status="success", finished_at=utc_now()
            )
            return await run_batch([company], CTX, session_factory=factory)

        result = _run(scenario())

        assert result.skipped == 1
        # The point of skipping is not doing the work.
        assert fake.calls == []

    def test_a_recent_failure_is_retried(
        self, tmp_path: Path, fake: _FakeStrategy
    ) -> None:
        """The property an ``updated_at`` column would have destroyed."""
        company = _http_company(0)

        async def scenario() -> BatchResult:
            factory = await _fresh_database(tmp_path / "retry.db")
            await _seed_run(
                factory, company=company, status="failed", finished_at=utc_now()
            )
            return await run_batch([company], CTX, session_factory=factory)

        result = _run(scenario())

        assert result.succeeded == 1
        assert fake.calls == [company.slug]

    def test_a_success_outside_the_window_is_retried(
        self, tmp_path: Path, fake: _FakeStrategy
    ) -> None:
        company = _http_company(0)

        async def scenario() -> BatchResult:
            factory = await _fresh_database(tmp_path / "stale-success.db")
            await _seed_run(
                factory,
                company=company,
                status="success",
                finished_at=utc_now() - timedelta(hours=30),
            )
            return await run_batch([company], CTX, session_factory=factory)

        result = _run(scenario())

        assert result.succeeded == 1

    def test_force_overrides_freshness(
        self, tmp_path: Path, fake: _FakeStrategy
    ) -> None:
        company = _http_company(0)

        async def scenario() -> BatchResult:
            factory = await _fresh_database(tmp_path / "force.db")
            await _seed_run(
                factory, company=company, status="success", finished_at=utc_now()
            )
            return await run_batch(
                [company],
                CTX,
                session_factory=factory,
                policy=BatchPolicy(force=True),
            )

        result = _run(scenario())

        assert result.succeeded == 1
        assert result.skipped == 0

    def test_a_skipped_company_writes_no_run_row(
        self, tmp_path: Path, fake: _FakeStrategy
    ) -> None:
        """``company_runs`` records attempts, not considerations."""
        company = _http_company(0)

        async def scenario() -> int:
            factory = await _fresh_database(tmp_path / "no-row.db")
            await _seed_run(
                factory, company=company, status="success", finished_at=utc_now()
            )
            await run_batch([company], CTX, session_factory=factory)
            async with factory() as session:
                return int(
                    (
                        await session.execute(
                            select(func.count()).select_from(models.CompanyRun)
                        )
                    ).scalar_one()
                )

        # Only the seeded row.
        assert _run(scenario()) == 1


# ---------------------------------------------------------------------
# Crash recovery
# ---------------------------------------------------------------------


class TestReaping:
    """A killed batch is recovered by the next one, with no checkpoint."""

    def test_an_abandoned_run_is_reaped_and_the_company_runs_again(
        self, tmp_path: Path, fake: _FakeStrategy
    ) -> None:
        company = _http_company(0)

        async def scenario() -> tuple[BatchResult, list[Any]]:
            factory = await _fresh_database(tmp_path / "reap.db")
            await _seed_run(
                factory,
                company=company,
                status="in_progress",
                started_at=utc_now() - timedelta(hours=3),
            )
            result = await run_batch([company], CTX, session_factory=factory)
            async with factory() as session:
                rows = (
                    await session.execute(
                        select(
                            models.CompanyRun.status,
                            models.CompanyRun.error_message,
                        ).order_by(models.CompanyRun.id)
                    )
                ).all()
            return result, list(rows)

        result, rows = _run(scenario())

        assert result.reaped == 1
        # The abandoned row was failed with the documented message, and a
        # fresh successful row sits beside it.
        assert rows[0].status == "failed"
        assert rows[0].error_message == "abandoned: batch terminated"
        assert rows[1].status == "success"
        assert result.succeeded == 1

    def test_a_recent_in_progress_run_is_left_alone(
        self, tmp_path: Path, fake: _FakeStrategy
    ) -> None:
        """The cutoff is real, not a blanket 'fail everything running'."""
        company = _http_company(0)

        async def scenario() -> BatchResult:
            factory = await _fresh_database(tmp_path / "no-reap.db")
            await _seed_run(
                factory,
                company=company,
                status="in_progress",
                started_at=utc_now(),
            )
            return await run_batch([company], CTX, session_factory=factory)

        assert _run(scenario()).reaped == 0


# ---------------------------------------------------------------------
# What ends up in the database
# ---------------------------------------------------------------------


class TestPersistedResult:
    """The batch's whole output is what lands in the two tables."""

    def test_a_successful_run_stores_its_urls_and_verdict(
        self, tmp_path: Path, fake: _FakeStrategy
    ) -> None:
        company = _http_company(0)
        urls = [f"{company.job_board_url}/jobs/{n}" for n in range(3)]

        async def three(c: Company) -> dict[str, Any]:
            return _report_for(c, urls)

        fake.handlers[company.slug] = three

        async def scenario() -> tuple[list[str], Any]:
            factory = await _fresh_database(tmp_path / "stored.db")
            await run_batch([company], CTX, session_factory=factory)
            async with factory() as session:
                stored = list(
                    (
                        await session.execute(
                            select(models.JobUrl.url).order_by(models.JobUrl.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                run = (
                    await session.execute(
                        select(
                            models.CompanyRun.url_count,
                            models.CompanyRun.verdict,
                            models.CompanyRun.batch_id,
                        )
                    )
                ).one()
            return stored, run

        stored, run = _run(scenario())

        assert stored == urls
        assert run.url_count == 3
        # `expected_jobs` is None on these fixtures, so the report's own
        # verdict is "unverified" — stored verbatim rather than recomputed.
        assert run.verdict == "unverified"
        assert run.batch_id

    def test_url_count_matches_the_rows_actually_stored(
        self, tmp_path: Path, fake: _FakeStrategy
    ) -> None:
        """A board emitting a duplicate records the de-duplicated count.

        Recording ``len(jobs)`` instead would write a number no
        ``select count(*) from job_urls`` could reproduce.
        """
        company = _http_company(0)
        duplicated = [
            f"{company.job_board_url}/jobs/1",
            f"{company.job_board_url}/jobs/1",
            f"{company.job_board_url}/jobs/2",
        ]

        async def dupes(c: Company) -> dict[str, Any]:
            return _report_for(c, duplicated)

        fake.handlers[company.slug] = dupes

        async def scenario() -> tuple[int, int | None]:
            factory = await _fresh_database(tmp_path / "dupes.db")
            await run_batch([company], CTX, session_factory=factory)
            async with factory() as session:
                rows = int(
                    (
                        await session.execute(
                            select(func.count()).select_from(models.JobUrl)
                        )
                    ).scalar_one()
                )
                # Deliberately not coerced: `url_count` is nullable
                # because a failed run records no count, so a None here
                # should fail the comparison below rather than raise.
                recorded = (
                    await session.execute(select(models.CompanyRun.url_count))
                ).scalar_one()
            return rows, recorded

        rows, recorded = _run(scenario())

        assert rows == 2
        assert recorded == rows

    def test_the_catalog_is_projected_before_any_run_starts(
        self, tmp_path: Path, fake: _FakeStrategy
    ) -> None:
        """Without this the first run of a new company hits an FK error."""
        companies = [_http_company(0), _browser_company(0)]

        async def scenario() -> set[str]:
            factory = await _fresh_database(tmp_path / "projection.db")
            await run_batch(companies, CTX, session_factory=factory)
            async with factory() as session:
                return set(
                    (await session.execute(select(models.Company.slug))).scalars().all()
                )

        assert _run(scenario()) == {c.slug for c in companies}

    def test_outcomes_follow_the_input_order(
        self, tmp_path: Path, fake: _FakeStrategy
    ) -> None:
        """``gather`` preserves input order regardless of completion."""
        companies = [_http_company(i) for i in range(5)]

        async def slow(c: Company) -> dict[str, Any]:
            # Reverse the completion order relative to the input order.
            await asyncio.sleep(0.01 * (5 - int(c.name.split()[-1])))
            return _report_for(c, [])

        for company in companies:
            fake.handlers[company.slug] = slow

        async def scenario() -> BatchResult:
            factory = await _fresh_database(tmp_path / "order.db")
            return await run_batch(companies, CTX, session_factory=factory)

        result = _run(scenario())

        assert [o.company.slug for o in result.outcomes] == [c.slug for c in companies]


# ---------------------------------------------------------------------
# The worker in isolation
# ---------------------------------------------------------------------


class TestRunCompany:
    """The unit of work is callable without a scheduler."""

    def test_run_company_persists_without_going_through_run_batch(
        self, tmp_path: Path, fake: _FakeStrategy
    ) -> None:
        """Dependency inversion: the worker takes its session factory.

        This is what lets the batch be tested without a database file on
        disk in the repo, and what keeps the worker from deciding where
        the database lives.
        """
        company = _http_company(0)

        async def scenario() -> tuple[CompanyOutcome, int]:
            factory = await _fresh_database(tmp_path / "worker.db")
            # The projection row the foreign keys require.
            async with factory() as session, session.begin():
                session.add(
                    models.Company(
                        slug=company.slug,
                        name=company.name,
                        job_board_url=company.job_board_url,
                        strategy=company.strategy,
                        expected_jobs=None,
                        synced_at=utc_now(),
                    )
                )
            outcome = await run_company(
                company,
                CTX,
                session_factory=factory,
                policy=BatchPolicy(),
                batch_id="manual",
            )
            async with factory() as session:
                count = int(
                    (
                        await session.execute(
                            select(func.count()).select_from(models.JobUrl)
                        )
                    ).scalar_one()
                )
            return outcome, count

        outcome, count = _run(scenario())

        assert outcome.status == "success"
        assert count == 1


class TestBatchId:
    """Ids are sortable and do not collide within a second."""

    def test_ids_are_unique_within_the_same_second(self) -> None:
        assert len({new_batch_id() for _ in range(50)}) == 50

    def test_ids_sort_chronologically(self) -> None:
        first = new_batch_id()
        second = new_batch_id()
        # Timestamp-first, so lexicographic order is chronological order.
        assert first[:15] <= second[:15]
