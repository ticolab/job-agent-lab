"""Observation tests against a real SQLite database file."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from job_discovery.domain.company import Company
from job_discovery.persistence import jobs_repo as discovery_jobs_repo
from job_discovery.persistence import models as discovery_models
from job_discovery.persistence.engine import create_engine, create_session_factory
from job_pipeline.persistence import contents_repo, jobs_repo, models
from job_pipeline.runner.observe import observe
from tests.unit.asyncio_harness import run_async, track_engine


async def _fresh_database(path: Path) -> async_sessionmaker[AsyncSession]:
    engine = track_engine(create_engine(path))
    async with engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)
    return create_session_factory(engine)


def test_observe_collapses_identity_and_leaves_departed_job_untouched(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        factory = await _fresh_database(tmp_path / "observe.db")
        company = Company(
            name="Acme",
            job_board_url="https://acme.example/jobs",
            sample_job_url="https://acme.example/jobs/1",
        )
        first_url = "https://ACME.example/jobs/1?utm_source=board"
        second_url = "https://acme.example/jobs/1"
        async with factory() as session, session.begin():
            session.add(
                discovery_models.Company(
                    slug=company.slug,
                    name=company.name,
                    job_board_url=company.job_board_url,
                    strategy=company.strategy,
                    synced_at=datetime(2026, 1, 1),
                )
            )
        async with factory() as session:
            await discovery_jobs_repo.replace_company_urls(
                session,
                company_slug=company.slug,
                urls=[first_url, second_url],
                captured_at=datetime(2026, 1, 1),
            )
        first = await observe(factory, [company])
        async with factory() as session:
            initial_job = (await session.execute(select(models.Job))).scalar_one()
            initial_first_seen = initial_job.first_seen_at
            initial_last_seen = initial_job.last_seen_at
            source_count = len(
                (await session.execute(select(models.JobSource))).scalars().all()
            )
        second = await observe(factory, [company])
        async with factory() as session:
            seen_job = (await session.execute(select(models.Job))).scalar_one()
            assert seen_job.first_seen_at == initial_first_seen
            assert seen_job.last_seen_at >= initial_last_seen
            seen_last_seen = seen_job.last_seen_at
        async with factory() as session:
            await discovery_jobs_repo.replace_company_urls(
                session,
                company_slug=company.slug,
                urls=[],
                captured_at=datetime(2026, 1, 2),
            )
        third = await observe(factory, [company])
        async with factory() as session:
            departed_job = (await session.execute(select(models.Job))).scalar_one()
            assert departed_job.last_seen_at == seen_last_seen

        assert first.new == 1
        assert first.reseen == 0
        assert first.collapsed == 1
        assert first.snapshot_keys == frozenset(
            {("acme", "https://acme.example/jobs/1")}
        )
        assert second.new == 0
        assert second.reseen == 1
        assert second.collapsed == 1
        assert source_count == 2
        assert third.companies[0].raw_urls == 0
        assert third.snapshot_keys == frozenset()

    run_async(scenario())


def test_observed_merged_alias_keeps_canonical_snapshot_eligible(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[frozenset[tuple[str, str]], list[int], list[str]]:
        factory = await _fresh_database(tmp_path / "merged-alias.db")
        company = Company(
            name="Acme",
            job_board_url="https://acme.example/jobs",
            sample_job_url="https://acme.example/jobs/1",
        )
        canonical_url = "https://acme.example/jobs/1"
        alias_url = "https://acme.example/jobs/2"
        async with factory() as session, session.begin():
            session.add(
                discovery_models.Company(
                    slug=company.slug,
                    name=company.name,
                    job_board_url=company.job_board_url,
                    strategy=company.strategy,
                    synced_at=datetime(2026, 1, 1),
                )
            )
        async with factory() as session:
            await jobs_repo.observe(
                session,
                company.slug,
                [(canonical_url, canonical_url), (alias_url, alias_url)],
                datetime(2026, 1, 1),
            )
        for job_id in (1, 2):
            async with factory() as session:
                await contents_repo.store(
                    session,
                    job_id,
                    contents_repo.AcquiredContent(
                        content_hash=f"hash-{job_id}",
                        fingerprint="same",
                        requisition_id="REQ-SAME",
                        resolved_url=canonical_url,
                        http_status=200,
                        method="main_content",
                        title="Engineer",
                        text="Posting text",
                        jsonld_json=None,
                        retrieved_at=datetime(2026, 1, 1),
                    ),
                )
        async with factory() as session:
            await jobs_repo.merge_duplicate(
                session,
                job_id=2,
                company_slug=company.slug,
                requisition_id="REQ-SAME",
                fingerprint="same",
            )
        async with factory() as session:
            await discovery_jobs_repo.replace_company_urls(
                session,
                company_slug=company.slug,
                urls=[alias_url],
                captured_at=datetime(2026, 1, 2),
            )
        summary = await observe(factory, [company])
        async with factory() as session:
            work = await jobs_repo.list_work(
                session,
                summary.snapshot_keys,
                datetime(2026, 1, 2),
                None,
            )
            sources = await jobs_repo.list_sources(session, 1)
        return (
            summary.snapshot_keys,
            [job.id for job in work],
            [source.url for source in sources],
        )

    snapshot, work, sources = run_async(scenario())
    assert snapshot == frozenset({("acme", "https://acme.example/jobs/1")})
    assert work == [1]
    assert sources == [
        "https://acme.example/jobs/1",
        "https://acme.example/jobs/2",
    ]


def test_unkeyable_url_is_skipped_without_aborting_observation(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[int, int, int, frozenset[tuple[str, str]]]:
        factory = await _fresh_database(tmp_path / "malformed.db")
        company = Company(
            name="Acme",
            job_board_url="https://acme.example/jobs",
            sample_job_url="https://acme.example/jobs/1",
        )
        async with factory() as session, session.begin():
            session.add(
                discovery_models.Company(
                    slug=company.slug,
                    name=company.name,
                    job_board_url=company.job_board_url,
                    strategy=company.strategy,
                    synced_at=datetime(2026, 1, 1),
                )
            )
        async with factory() as session:
            await discovery_jobs_repo.replace_company_urls(
                session,
                company_slug=company.slug,
                urls=["https://acme.example/jobs/1", "javascript:void(0)"],
                captured_at=datetime(2026, 1, 1),
            )
        summary = await observe(factory, [company])
        report = summary.companies[0]
        return report.new, report.malformed, report.collapsed, summary.snapshot_keys

    assert run_async(scenario()) == (
        1,
        1,
        0,
        frozenset({("acme", "https://acme.example/jobs/1")}),
    )
