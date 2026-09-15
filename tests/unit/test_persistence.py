"""Unit tests for the persistence layer (Phase 1).

Every test runs against a **real SQLite file** under ``tmp_path`` rather
than ``:memory:``. That is not incidental: an in-memory database
exercises neither WAL journal mode nor ``busy_timeout``, which are the
two settings that matter once the batch scheduler runs workers
concurrently, and it would let a regression in
:func:`vacantes.persistence.engine._apply_pragmas` pass unnoticed.
:class:`TestEnginePragmas` asserts them directly for that reason.

Async tests are driven through :func:`_run`, which executes each
coroutine on a background thread with its own fresh event loop. The repo
does not use ``pytest-asyncio``; see
:mod:`tests.unit.test_prefiltered` for the full rationale — in short,
the snapshot suite drives Chromium through Playwright's sync API and
leaves a running-loop registration on the main thread's
:mod:`asyncio.events` state, which makes both ``asyncio.run`` and a
main-thread ``run_until_complete`` raise for the rest of the session.

Each test body is a single coroutine so the async engine is created,
used, and disposed inside one event loop. An engine outliving its loop
is the classic source of "attached to a different loop" failures.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.unit.asyncio_harness import run_async as _run
from tests.unit.asyncio_harness import run_in_thread, track_engine
from vacantes.domain.company import Company
from vacantes.persistence import catalog_repo, jobs_repo, models, runs_repo
from vacantes.persistence.engine import (
    create_engine,
    create_session_factory,
    database_url,
)
from vacantes.persistence.timestamps import as_naive_utc, utc_now

REPO_ROOT = Path(__file__).resolve().parents[2]


async def _fresh_database(path: Path) -> async_sessionmaker[AsyncSession]:
    """Create the schema at *path* and return a session factory.

    Uses ``Base.metadata.create_all`` rather than the Alembic
    migrations, so a repository test fails for its own reason and not
    because a migration is broken. :class:`TestMigrationParity` is what
    ties the two together.

    The engine is handed to
    :func:`~tests.unit.asyncio_harness.track_engine`, so ``_run``
    disposes it on the scenario's own loop; callers do not manage its
    lifecycle.
    """
    engine = track_engine(create_engine(path))
    async with engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)
    return create_session_factory(engine)


async def _seed_company(
    factory: async_sessionmaker[AsyncSession], slug: str = "acme"
) -> str:
    """Insert a minimal projection row so foreign keys are satisfiable."""
    async with factory() as session, session.begin():
        session.add(
            models.Company(
                slug=slug,
                name=slug.title(),
                job_board_url=f"https://{slug}.example/careers",
                strategy="dom",
                expected_jobs=3,
                synced_at=utc_now(),
            )
        )
    return slug


def _company(name: str = "Acme Corp", **overrides: Any) -> Company:
    """Build a valid domain ``Company`` for projection tests."""
    fields: dict[str, Any] = {
        "name": name,
        "job_board_url": "https://acme.example/careers",
        "sample_job_url": "https://acme.example/careers/jobs/1",
        "expected_jobs": 3,
    }
    fields.update(overrides)
    return Company(**fields)


class TestEnginePragmas:
    """The four pragmas are correctness, not tuning."""

    def test_pragmas_are_applied_to_every_connection(self, tmp_path: Path) -> None:
        """WAL, busy_timeout, synchronous, and foreign_keys are all set.

        Asserted on a real file because ``journal_mode=WAL`` silently
        degrades to ``memory`` for an in-memory database, so this
        assertion would be vacuous under ``:memory:``.
        """

        async def scenario() -> dict[str, Any]:
            engine = create_engine(tmp_path / "pragmas.db")
            try:
                async with engine.connect() as connection:
                    return {
                        name: (
                            await connection.execute(text(f"PRAGMA {name}"))
                        ).scalar()
                        for name in (
                            "journal_mode",
                            "busy_timeout",
                            "synchronous",
                            "foreign_keys",
                        )
                    }
            finally:
                await engine.dispose()

        observed = _run(scenario())
        assert observed["journal_mode"] == "wal"
        assert observed["busy_timeout"] == 5000
        assert observed["synchronous"] == 1  # NORMAL
        assert observed["foreign_keys"] == 1

    def test_foreign_keys_are_actually_enforced(self, tmp_path: Path) -> None:
        """Without the pragma, every REFERENCES clause is inert.

        This is the test that would catch the pragma silently not being
        applied: SQLite's default is off, and with it off the insert
        below succeeds and leaves an orphan row.
        """

        async def scenario() -> None:
            factory = await _fresh_database(tmp_path / "fk.db")
            async with factory() as session, session.begin():
                session.add(
                    models.JobUrl(
                        company_slug="does-not-exist",
                        url="https://acme.example/careers/jobs/1",
                        captured_at=utc_now(),
                    )
                )

        with pytest.raises(IntegrityError):
            _run(scenario())


class TestAtomicReplace:
    """``replace_company_urls`` is the only writer of ``job_urls``."""

    def test_replace_leaves_no_stale_rows(self, tmp_path: Path) -> None:
        """A second replace fully supersedes the first.

        The property that makes delete-and-insert preferable to
        upsert-plus-prune: there is no "which rows did I not see this
        time" bookkeeping that can leave a closed posting behind.
        """

        async def scenario() -> tuple[str, ...]:
            factory = await _fresh_database(tmp_path / "replace.db")
            slug = await _seed_company(factory)
            async with factory() as session:
                await jobs_repo.replace_company_urls(
                    session,
                    company_slug=slug,
                    urls=["https://a.example/1", "https://a.example/2"],
                    captured_at=utc_now(),
                )
                await jobs_repo.replace_company_urls(
                    session,
                    company_slug=slug,
                    urls=["https://a.example/2", "https://a.example/3"],
                    captured_at=utc_now(),
                )
                return await jobs_repo.list_company_urls(session, company_slug=slug)

        assert _run(scenario()) == ("https://a.example/2", "https://a.example/3")

    def test_replace_with_empty_set_clears_the_previous_one(
        self, tmp_path: Path
    ) -> None:
        """A board whose postings all closed reports zero, not nothing.

        An empty input must still run the delete. Treating it as a
        no-op guard would leave the previous snapshot in place and the
        database would claim postings are open that are not.
        """

        async def scenario() -> tuple[int, tuple[str, ...]]:
            factory = await _fresh_database(tmp_path / "empty.db")
            slug = await _seed_company(factory)
            async with factory() as session:
                await jobs_repo.replace_company_urls(
                    session,
                    company_slug=slug,
                    urls=["https://a.example/1"],
                    captured_at=utc_now(),
                )
                written = await jobs_repo.replace_company_urls(
                    session, company_slug=slug, urls=[], captured_at=utc_now()
                )
                stored = await jobs_repo.list_company_urls(session, company_slug=slug)
                return written, stored

        assert _run(scenario()) == (0, ())

    def test_duplicates_collapse_and_keep_strategy_order(self, tmp_path: Path) -> None:
        """``dict.fromkeys`` de-duplicates without reordering.

        A board that lists the same posting in two sections is recorded
        once, and the order the strategy emitted survives — which a
        ``set`` would have destroyed.
        """

        async def scenario() -> tuple[int, tuple[str, ...]]:
            factory = await _fresh_database(tmp_path / "dedupe.db")
            slug = await _seed_company(factory)
            async with factory() as session:
                written = await jobs_repo.replace_company_urls(
                    session,
                    company_slug=slug,
                    urls=[
                        "https://a.example/c",
                        "https://a.example/a",
                        "https://a.example/c",
                        "https://a.example/b",
                    ],
                    captured_at=utc_now(),
                )
                return written, await jobs_repo.list_company_urls(
                    session, company_slug=slug
                )

        written, stored = _run(scenario())
        assert written == 3
        assert stored == (
            "https://a.example/c",
            "https://a.example/a",
            "https://a.example/b",
        )

    def test_failure_mid_transaction_preserves_the_previous_snapshot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The transaction is the failure boundary.

        The delete and the insert are one unit. Here the insert raises
        after the delete has already been issued; the rollback must
        restore the prior set rather than leave the company with no
        URLs at all. A reader concurrent with a crash must never see an
        empty board that is actually populated.

        The failure is injected by replacing the ``JobUrl`` the
        repository constructs, which stands in for any error during row
        construction or flush.
        """

        async def scenario() -> tuple[str, ...]:
            factory = await _fresh_database(tmp_path / "rollback.db")
            slug = await _seed_company(factory)
            async with factory() as session:
                await jobs_repo.replace_company_urls(
                    session,
                    company_slug=slug,
                    urls=["https://a.example/keep-1", "https://a.example/keep-2"],
                    captured_at=utc_now(),
                )

                def _explode(_rows: Any) -> None:
                    raise RuntimeError("injected failure during insert")

                # Patched on the session rather than on the model: the
                # repository builds its DELETE from ``JobUrl`` too, so
                # replacing the class would break statement construction
                # before the transaction ever opened.
                monkeypatch.setattr(session, "add_all", _explode)

                with pytest.raises(RuntimeError, match="injected failure"):
                    await jobs_repo.replace_company_urls(
                        session,
                        company_slug=slug,
                        urls=["https://a.example/new-1", "https://a.example/new-2"],
                        captured_at=utc_now(),
                    )

                monkeypatch.undo()
                return await jobs_repo.list_company_urls(session, company_slug=slug)

        assert _run(scenario()) == (
            "https://a.example/keep-1",
            "https://a.example/keep-2",
        )


class TestRunLifecycle:
    """``company_runs`` records attempts, not considerations."""

    def test_start_then_finish_records_a_success(self, tmp_path: Path) -> None:
        async def scenario() -> models.CompanyRun:
            factory = await _fresh_database(tmp_path / "lifecycle.db")
            slug = await _seed_company(factory)
            async with factory() as session:
                run_id = await runs_repo.start_run(
                    session, company_slug=slug, batch_id="batch-1"
                )
                await runs_repo.finish_run(
                    session, run_id=run_id, url_count=7, verdict="match"
                )
                result = await session.execute(
                    select(models.CompanyRun).where(models.CompanyRun.id == run_id)
                )
                return result.scalar_one()

        run = _run(scenario())
        assert run.status == "success"
        assert run.url_count == 7
        assert run.verdict == "match"
        assert run.finished_at is not None
        assert run.error_message is None

    def test_start_run_does_not_depend_on_expire_on_commit(
        self, tmp_path: Path
    ) -> None:
        """The returned id is read inside the transaction, not after it.

        ``create_session_factory`` sets ``expire_on_commit=False``, and an
        earlier ``start_run`` relied on that: it read ``run.id`` *after*
        the commit. Under SQLAlchemy's default ``True`` the instance is
        expired on commit and the attribute access becomes a lazy refresh
        against a closed transaction — ``MissingGreenlet`` on the async
        engine. A repository must not be correct under only one factory
        configuration, so this test drives it through a plain
        ``async_sessionmaker(engine)`` with the default and asserts the
        id still comes back.
        """

        async def scenario() -> int:
            engine = track_engine(create_engine(tmp_path / "default-factory.db"))
            async with engine.begin() as connection:
                await connection.run_sync(models.Base.metadata.create_all)
            # Deliberately NOT create_session_factory: SQLAlchemy's default
            # expire_on_commit=True is the configuration under test.
            factory: async_sessionmaker[AsyncSession] = async_sessionmaker(engine)
            slug = await _seed_company(factory)
            async with factory() as session:
                return await runs_repo.start_run(
                    session, company_slug=slug, batch_id="batch-default"
                )

        assert isinstance(_run(scenario()), int)

    def test_fail_run_leaves_count_and_verdict_null(self, tmp_path: Path) -> None:
        """A failed run observed no count, so it must not claim one.

        Writing 0 would be indistinguishable from a board that was
        successfully found to be empty, and the two demand opposite
        responses: retry versus accept.
        """

        async def scenario() -> models.CompanyRun:
            factory = await _fresh_database(tmp_path / "failure.db")
            slug = await _seed_company(factory)
            async with factory() as session:
                run_id = await runs_repo.start_run(
                    session, company_slug=slug, batch_id="batch-1"
                )
                await runs_repo.fail_run(
                    session, run_id=run_id, error="TimeoutError('board hung')"
                )
                result = await session.execute(
                    select(models.CompanyRun).where(models.CompanyRun.id == run_id)
                )
                return result.scalar_one()

        run = _run(scenario())
        assert run.status == "failed"
        assert run.url_count is None
        assert run.verdict is None
        assert run.error_message == "TimeoutError('board hung')"

    def test_status_check_constraint_rejects_an_unknown_value(
        self, tmp_path: Path
    ) -> None:
        """The database is the last guard on the status vocabulary."""

        async def scenario() -> None:
            factory = await _fresh_database(tmp_path / "status.db")
            slug = await _seed_company(factory)
            async with factory() as session, session.begin():
                session.add(
                    models.CompanyRun(
                        company_slug=slug,
                        batch_id="batch-1",
                        status="nearly_done",
                        started_at=utc_now(),
                    )
                )

        with pytest.raises(IntegrityError):
            _run(scenario())


class TestFreshness:
    """The skip decision counts successes, never attempts."""

    def test_a_recent_success_is_fresh(self, tmp_path: Path) -> None:
        async def scenario() -> bool:
            factory = await _fresh_database(tmp_path / "fresh.db")
            slug = await _seed_company(factory)
            async with factory() as session:
                run_id = await runs_repo.start_run(
                    session, company_slug=slug, batch_id="batch-1"
                )
                await runs_repo.finish_run(
                    session, run_id=run_id, url_count=1, verdict="match"
                )
                return await runs_repo.has_fresh_success(
                    session,
                    company_slug=slug,
                    since=utc_now() - timedelta(hours=20),
                )

        assert _run(scenario()) is True

    def test_a_failure_is_never_fresh(self, tmp_path: Path) -> None:
        """The rule the whole retry story rests on.

        If failures counted toward freshness, a board that broke this
        morning would look "done" this afternoon and would stop being
        retried — the failure would become permanent and silent.
        """

        async def scenario() -> bool:
            factory = await _fresh_database(tmp_path / "stale-failure.db")
            slug = await _seed_company(factory)
            async with factory() as session:
                run_id = await runs_repo.start_run(
                    session, company_slug=slug, batch_id="batch-1"
                )
                await runs_repo.fail_run(session, run_id=run_id, error="boom")
                return await runs_repo.has_fresh_success(
                    session,
                    company_slug=slug,
                    since=utc_now() - timedelta(hours=20),
                )

        assert _run(scenario()) is False

    def test_an_in_progress_run_is_never_fresh(self, tmp_path: Path) -> None:
        """An unfinished run has no ``finished_at`` to compare."""

        async def scenario() -> bool:
            factory = await _fresh_database(tmp_path / "in-progress.db")
            slug = await _seed_company(factory)
            async with factory() as session:
                await runs_repo.start_run(
                    session, company_slug=slug, batch_id="batch-1"
                )
                return await runs_repo.has_fresh_success(
                    session,
                    company_slug=slug,
                    since=utc_now() - timedelta(hours=20),
                )

        assert _run(scenario()) is False

    def test_a_success_outside_the_window_is_not_fresh(self, tmp_path: Path) -> None:
        async def scenario() -> bool:
            factory = await _fresh_database(tmp_path / "window.db")
            slug = await _seed_company(factory)
            async with factory() as session:
                run_id = await runs_repo.start_run(
                    session, company_slug=slug, batch_id="batch-1"
                )
                await runs_repo.finish_run(
                    session, run_id=run_id, url_count=1, verdict="match"
                )
                # A cutoff in the future: nothing can satisfy it.
                return await runs_repo.has_fresh_success(
                    session,
                    company_slug=slug,
                    since=utc_now() + timedelta(hours=1),
                )

        assert _run(scenario()) is False

    def test_freshness_is_scoped_to_one_company(self, tmp_path: Path) -> None:
        async def scenario() -> bool:
            factory = await _fresh_database(tmp_path / "scope.db")
            await _seed_company(factory, slug="acme")
            await _seed_company(factory, slug="globex")
            async with factory() as session:
                run_id = await runs_repo.start_run(
                    session, company_slug="acme", batch_id="batch-1"
                )
                await runs_repo.finish_run(
                    session, run_id=run_id, url_count=1, verdict="match"
                )
                return await runs_repo.has_fresh_success(
                    session,
                    company_slug="globex",
                    since=utc_now() - timedelta(hours=20),
                )

        assert _run(scenario()) is False

    def test_an_aware_cutoff_compares_correctly(self, tmp_path: Path) -> None:
        """The timezone footgun this layer normalizes away.

        Stored timestamps are naive UTC. An aware cutoff serializes with
        a ``+00:00`` suffix, and SQLite's lexicographic comparison would
        misjudge it. Normalizing at the boundary means a caller holding
        an aware datetime cannot silently corrupt the skip decision.
        """

        async def scenario() -> bool:
            factory = await _fresh_database(tmp_path / "aware.db")
            slug = await _seed_company(factory)
            async with factory() as session:
                run_id = await runs_repo.start_run(
                    session, company_slug=slug, batch_id="batch-1"
                )
                await runs_repo.finish_run(
                    session, run_id=run_id, url_count=1, verdict="match"
                )
                return await runs_repo.has_fresh_success(
                    session,
                    company_slug=slug,
                    since=datetime.now(UTC) - timedelta(hours=20),
                )

        assert _run(scenario()) is True


class TestReaper:
    """Crash recovery falls out of the freshness rule."""

    def test_stale_in_progress_runs_are_reaped(self, tmp_path: Path) -> None:
        """An abandoned run is identified by a start with no finish."""

        async def scenario() -> tuple[int, models.CompanyRun]:
            factory = await _fresh_database(tmp_path / "reap.db")
            slug = await _seed_company(factory)
            async with factory() as session:
                run_id = await runs_repo.start_run(
                    session, company_slug=slug, batch_id="batch-1"
                )
                reaped = await runs_repo.reap_stale_runs(
                    session, older_than=utc_now() + timedelta(hours=1)
                )
                result = await session.execute(
                    select(models.CompanyRun).where(models.CompanyRun.id == run_id)
                )
                return reaped, result.scalar_one()

        reaped, run = _run(scenario())
        assert reaped == 1
        assert run.status == "failed"
        assert run.error_message == runs_repo.ABANDONED_MESSAGE
        assert run.finished_at is not None

    def test_a_running_run_inside_the_cutoff_is_left_alone(
        self, tmp_path: Path
    ) -> None:
        """Reaping a live run would fail a board that is still working.

        The cutoff is the caller's judgement precisely because it must
        exceed the slowest board's runtime.
        """

        async def scenario() -> tuple[int, models.CompanyRun]:
            factory = await _fresh_database(tmp_path / "live.db")
            slug = await _seed_company(factory)
            async with factory() as session:
                run_id = await runs_repo.start_run(
                    session, company_slug=slug, batch_id="batch-1"
                )
                reaped = await runs_repo.reap_stale_runs(
                    session, older_than=utc_now() - timedelta(hours=1)
                )
                result = await session.execute(
                    select(models.CompanyRun).where(models.CompanyRun.id == run_id)
                )
                return reaped, result.scalar_one()

        reaped, run = _run(scenario())
        assert reaped == 0
        assert run.status == "in_progress"

    def test_completed_runs_are_never_reaped(self, tmp_path: Path) -> None:
        """Only ``in_progress`` rows are candidates.

        A success reaped into a failure would discard the record that
        makes the next batch skip the company, and the corpus would be
        re-extracted from scratch after every crash.
        """

        async def scenario() -> tuple[int, str, str]:
            factory = await _fresh_database(tmp_path / "completed.db")
            await _seed_company(factory, slug="acme")
            await _seed_company(factory, slug="globex")
            async with factory() as session:
                ok_id = await runs_repo.start_run(
                    session, company_slug="acme", batch_id="batch-1"
                )
                await runs_repo.finish_run(
                    session, run_id=ok_id, url_count=2, verdict="match"
                )
                bad_id = await runs_repo.start_run(
                    session, company_slug="globex", batch_id="batch-1"
                )
                await runs_repo.fail_run(session, run_id=bad_id, error="boom")

                reaped = await runs_repo.reap_stale_runs(
                    session, older_than=utc_now() + timedelta(hours=1)
                )
                rows = await session.execute(
                    select(models.CompanyRun).order_by(models.CompanyRun.id)
                )
                statuses = [row.status for row in rows.scalars().all()]
                return reaped, statuses[0], statuses[1]

        reaped, first, second = _run(scenario())
        assert reaped == 0
        assert (first, second) == ("success", "failed")


class TestCatalogProjection:
    """``companies`` is a projection, never an owner."""

    def test_sync_inserts_rows_keyed_by_slug(self, tmp_path: Path) -> None:
        async def scenario() -> models.Company:
            factory = await _fresh_database(tmp_path / "sync.db")
            async with factory() as session:
                await catalog_repo.sync_catalog(session, [_company()])
                result = await session.execute(select(models.Company))
                return result.scalar_one()

        row = _run(scenario())
        assert row.slug == "acme_corp"
        assert row.name == "Acme Corp"
        assert row.strategy == "dom"
        assert row.expected_jobs == 3

    def test_resync_updates_in_place_without_duplicating(self, tmp_path: Path) -> None:
        """The sync runs at the start of every batch, so it must upsert.

        An insert-only sync would violate the primary key on the second
        batch; a delete-then-insert would cascade into ``job_urls`` and
        silently discard the dataset.
        """

        async def scenario() -> tuple[int, int | None]:
            factory = await _fresh_database(tmp_path / "resync.db")
            async with factory() as session:
                await catalog_repo.sync_catalog(session, [_company()])
                await catalog_repo.sync_catalog(session, [_company(expected_jobs=11)])
                rows = await session.execute(select(models.Company))
                projected = list(rows.scalars().all())
                return len(projected), projected[0].expected_jobs

        assert _run(scenario()) == (1, 11)

    def test_resync_preserves_existing_job_urls(self, tmp_path: Path) -> None:
        """A routine catalog sync must not disturb the dataset.

        This is the test that would catch someone "simplifying"
        ``sync_catalog`` into a delete-and-insert: the cascade would
        take every URL and run with it.
        """

        async def scenario() -> tuple[str, ...]:
            factory = await _fresh_database(tmp_path / "preserve.db")
            async with factory() as session:
                await catalog_repo.sync_catalog(session, [_company()])
                await jobs_repo.replace_company_urls(
                    session,
                    company_slug="acme_corp",
                    urls=["https://a.example/1", "https://a.example/2"],
                    captured_at=utc_now(),
                )
                await catalog_repo.sync_catalog(session, [_company()])
                return await jobs_repo.list_company_urls(
                    session, company_slug="acme_corp"
                )

        assert _run(scenario()) == ("https://a.example/1", "https://a.example/2")

    def test_projection_slug_matches_the_domain_entity(self) -> None:
        """The projection key is the entity's own identity.

        ``Company.slug`` is a derived property, so the ``companies``
        primary key, the output filename, and the snapshot directory are
        the same string by construction rather than by three callers
        agreeing to call the same helper.
        """
        assert _company("Growth Acceleration Partners").slug == (
            "growth_acceleration_partners"
        )


class TestTimestampNormalization:
    """Naive UTC is an invariant of this layer, not a convention."""

    def test_an_aware_captured_at_is_stored_naive(self, tmp_path: Path) -> None:
        async def scenario() -> datetime:
            factory = await _fresh_database(tmp_path / "tz.db")
            slug = await _seed_company(factory)
            aware = datetime(2026, 3, 1, 12, 30, tzinfo=UTC)
            async with factory() as session:
                await jobs_repo.replace_company_urls(
                    session,
                    company_slug=slug,
                    urls=["https://a.example/1"],
                    captured_at=aware,
                )
                result = await session.execute(select(models.JobUrl.captured_at))
                return result.scalar_one()

        stored = _run(scenario())
        assert stored.tzinfo is None
        assert stored == datetime(2026, 3, 1, 12, 30)

    def test_a_non_utc_zone_is_converted_not_truncated(self) -> None:
        """An offset is applied, not discarded.

        Dropping the tzinfo without converting would shift the instant
        by the offset — the kind of error that shows up as a board
        skipped a few hours early and is nearly impossible to trace.
        """
        aware = datetime(2026, 3, 1, 12, 30, tzinfo=timezone(timedelta(hours=-6)))
        assert as_naive_utc(aware) == datetime(2026, 3, 1, 18, 30)

    def test_a_naive_value_passes_through_unchanged(self) -> None:
        naive = datetime(2026, 3, 1, 12, 30)
        assert as_naive_utc(naive) is naive

    def test_utc_now_is_naive(self) -> None:
        assert utc_now().tzinfo is None


class TestMigrationParity:
    """The migration and the models must describe the same schema.

    ``_fresh_database`` builds the schema from ``Base.metadata`` so that
    a repository test fails for its own reason. That leaves a real gap:
    the application would run against a database built by Alembic, and
    nothing would notice if the two drifted. A model field added without
    a migration is exactly the change that passes every other test in
    this file and then fails on the operator's machine.

    ``compare_metadata`` is Alembic's own autogenerate diff, so this
    asserts there is nothing left to generate.
    """

    def test_alembic_head_matches_the_declarative_models(self, tmp_path: Path) -> None:
        from alembic import command
        from alembic.autogenerate import compare_metadata
        from alembic.config import Config
        from alembic.migration import MigrationContext
        from sqlalchemy import create_engine as create_sync_engine

        database_file = tmp_path / "migrated.db"

        def apply_and_diff() -> list[Any]:
            config = Config(str(REPO_ROOT / "alembic.ini"))
            config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
            # An explicit URL wins over the settings default, which is
            # what keeps this test off the operator's real database.
            # It must name the async driver: env.py builds an async
            # engine, and a bare `sqlite://` URL fails there.
            config.set_main_option("sqlalchemy.url", database_url(database_file))
            command.upgrade(config, "head")

            engine = create_sync_engine(f"sqlite:///{database_file}")
            try:
                with engine.connect() as connection:
                    context = MigrationContext.configure(connection)
                    return list(compare_metadata(context, models.Base.metadata))
            finally:
                engine.dispose()

        # Alembic's async env.py calls asyncio.run internally, so this
        # runs on its own thread for the same reason the async tests do.
        assert run_in_thread(apply_and_diff) == []
