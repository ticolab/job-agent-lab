"""Focused repository tests against a real SQLite database file."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import job_pipeline.persistence.attempts_repo as attempts_repo
import job_pipeline.persistence.contents_repo as contents_repo
import job_pipeline.persistence.decisions_repo as decisions_repo
import job_pipeline.persistence.jobs_repo as jobs_repo
import job_pipeline.persistence.models as models
import job_pipeline.persistence.results_repo as results_repo
import job_pipeline.persistence.runs_repo as runs_repo
from job_discovery.persistence import models as discovery_models
from job_discovery.persistence.engine import create_engine, create_session_factory
from tests.unit.asyncio_harness import run_async, track_engine


async def _fresh_database(path: Path) -> async_sessionmaker[AsyncSession]:
    engine = track_engine(create_engine(path))
    async with engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)
    return cast(async_sessionmaker[AsyncSession], create_session_factory(engine))


async def _seed_company(
    factory: async_sessionmaker[AsyncSession], slug: str = "acme"
) -> None:
    async with factory() as session, session.begin():
        session.add(
            discovery_models.Company(
                slug=slug,
                name=slug.title(),
                job_board_url=f"https://{slug}.example/jobs",
                strategy="dom",
                synced_at=datetime(2026, 1, 1),
            )
        )


async def _seed_job(
    factory: async_sessionmaker[AsyncSession], slug: str = "acme"
) -> int:
    await _seed_company(factory, slug)
    async with factory() as session:
        await jobs_repo.observe(
            session,
            slug,
            [("https://acme.example/jobs/1", "https://acme.example/jobs/1")],
            datetime(2026, 1, 1, tzinfo=UTC),
        )
        rows = await session.execute(select(models.Job.id))
        return cast(int, rows.scalar_one())


async def _seed_attempt_and_content(
    factory: async_sessionmaker[AsyncSession], job_id: int
) -> tuple[int, int]:
    async with factory() as session:
        await runs_repo.start_run(
            session,
            run_id="run-1",
            selection="all",
            model="gpt-4.1-mini",
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        attempt_id = await attempts_repo.start(
            session,
            job_id=job_id,
            run_id="run-1",
            trigger="scheduled",
            reason=None,
            model="gpt-4.1-mini",
            policy_version="policy-1",
            extractor_version="extractor-1",
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        content_id = await contents_repo.store(
            session,
            job_id,
            contents_repo.AcquiredContent(
                content_hash="hash-1",
                fingerprint="fingerprint-1",
                requisition_id="REQ-1",
                resolved_url="https://acme.example/jobs/1",
                http_status=200,
                method="main_content",
                title="Engineer",
                text="Posting text",
                jsonld_json=None,
                retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        )
        return attempt_id, content_id


def test_observe_is_idempotent_and_updates_last_seen(tmp_path: Path) -> None:
    async def scenario() -> tuple[
        jobs_repo.ObserveSummary, jobs_repo.ObserveSummary, int
    ]:
        factory = await _fresh_database(tmp_path / "observe.db")
        await _seed_company(factory)
        first_at = datetime(2026, 1, 1, tzinfo=UTC)
        second_at = first_at + timedelta(days=1)
        observations = [
            ("key-1", "https://acme.example/jobs/1"),
            ("key-1", "https://acme.example/jobs/1?source=board"),
        ]
        async with factory() as session:
            first = await jobs_repo.observe(session, "acme", observations, first_at)
            second = await jobs_repo.observe(session, "acme", observations, second_at)
            job = (await session.execute(select(models.Job))).scalar_one()
            sources = list(
                (
                    await session.execute(
                        select(models.JobSource).order_by(models.JobSource.id)
                    )
                ).scalars()
            )
            assert job.first_seen_at == datetime(2026, 1, 1)
            assert job.last_seen_at == datetime(2026, 1, 2)
            assert all(
                source.last_seen_at == datetime(2026, 1, 2) for source in sources
            )
            return first, second, len(sources)

    first, second, source_count = run_async(scenario())
    assert first == jobs_repo.ObserveSummary(new=1, reseen=0)
    assert second == jobs_repo.ObserveSummary(new=0, reseen=1)
    assert source_count == 2


def test_claim_has_one_winner_under_concurrency(tmp_path: Path) -> None:
    async def scenario() -> tuple[bool, bool]:
        factory = await _fresh_database(tmp_path / "claim.db")
        job_id = await _seed_job(factory)

        async def claimant() -> bool:
            async with factory() as session:
                return cast(bool, await jobs_repo.claim(session, job_id))

        first, second = await asyncio.gather(claimant(), claimant())
        return first, second

    assert sorted(run_async(scenario())) == [False, True]


def test_claim_rechecks_retry_due_time(tmp_path: Path) -> None:
    async def scenario() -> bool:
        factory = await _fresh_database(tmp_path / "retry-due-claim.db")
        job_id = await _seed_job(factory)
        async with factory() as session:
            await jobs_repo.claim(session, job_id)
        async with factory() as session:
            await jobs_repo.release(
                session,
                job_id,
                status="retry",
                next_phase="fetch",
                next_attempt_after=datetime(2099, 1, 1),
            )
        async with factory() as session:
            return cast(bool, await jobs_repo.claim(session, job_id))

    assert not run_async(scenario())


@pytest.mark.parametrize(
    ("status", "next_attempt_after"),
    [("retry", None), ("failed", datetime(2099, 1, 1))],
)
def test_release_enforces_retry_due_invariant(
    tmp_path: Path, status: str, next_attempt_after: datetime | None
) -> None:
    async def scenario() -> None:
        factory = await _fresh_database(tmp_path / f"invalid-{status}.db")
        job_id = await _seed_job(factory)
        async with factory() as session:
            await jobs_repo.claim(session, job_id)
        async with factory() as session:
            await jobs_repo.release(
                session,
                job_id,
                status=status,
                next_phase="fetch",
                next_attempt_after=next_attempt_after,
            )

    with pytest.raises(
        ValueError, match="next_attempt_after must be set exactly for retry jobs"
    ):
        run_async(scenario())


def test_database_enforces_retry_due_invariant(tmp_path: Path) -> None:
    async def scenario() -> None:
        factory = await _fresh_database(tmp_path / "retry-due-constraint.db")
        job_id = await _seed_job(factory)
        async with factory() as session, session.begin():
            await session.execute(
                update(models.Job)
                .where(models.Job.id == job_id)
                .values(status="failed", next_attempt_after=datetime(2099, 1, 1))
            )

    with pytest.raises(IntegrityError):
        run_async(scenario())


def test_content_storage_is_idempotent_and_identity_is_searchable(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[int, int, int, int]:
        factory = await _fresh_database(tmp_path / "content.db")
        job_id = await _seed_job(factory)
        acquired = contents_repo.AcquiredContent(
            content_hash="hash-1",
            fingerprint="fingerprint-1",
            requisition_id="REQ-1",
            resolved_url="https://acme.example/jobs/1",
            http_status=200,
            method="main_content",
            title="Engineer",
            text="Posting text",
            jsonld_json="{}",
            retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        async with factory() as session:
            first = await contents_repo.store(session, job_id, acquired)
            second = await contents_repo.store(session, job_id, acquired)
            by_req = await jobs_repo.find_by_requisition(
                session, "acme", "REQ-1", excluding_job_id=job_id + 1
            )
            by_fingerprint = await jobs_repo.find_by_fingerprint(
                session, "acme", "fingerprint-1", excluding_job_id=job_id + 1
            )
            excluding_self = await jobs_repo.find_by_fingerprint(
                session,
                "acme",
                "fingerprint-1",
                excluding_job_id=job_id,
            )
            latest = await contents_repo.latest(session, job_id)
            assert latest is not None
            assert latest.retrieved_at.tzinfo is None
            assert by_req is not None
            assert by_fingerprint is not None
            assert excluding_self is None
            return first, second, by_req.id, by_fingerprint.id

    first, second, requisition_job, fingerprint_job = run_async(scenario())
    assert first == second
    assert requisition_job == fingerprint_job


def test_accept_supersedes_current_decision_and_result(tmp_path: Path) -> None:
    async def scenario() -> tuple[str, str, int, int]:
        factory = await _fresh_database(tmp_path / "current.db")
        job_id = await _seed_job(factory)
        attempt_id, content_id = await _seed_attempt_and_content(factory, job_id)
        async with factory() as session:
            await decisions_repo.accept(
                session,
                job_id=job_id,
                attempt_id=attempt_id,
                content_id=content_id,
                decision=decisions_repo.DecisionInput(
                    decision="unknown",
                    reason="not stated",
                    evidence_json="[]",
                    policy_version="policy-1",
                ),
                decided_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            await decisions_repo.accept(
                session,
                job_id=job_id,
                attempt_id=attempt_id,
                content_id=content_id,
                decision=decisions_repo.DecisionInput(
                    decision="eligible",
                    reason="Costa Rica",
                    evidence_json='["Costa Rica"]',
                    policy_version="policy-2",
                ),
                decided_at=datetime(2026, 1, 2, tzinfo=UTC),
            )
            await results_repo.accept(
                session,
                job_id=job_id,
                attempt_id=attempt_id,
                content_id=content_id,
                result=results_repo.ResultInput("extractor-1", 1, "{}"),
                accepted_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            await results_repo.accept(
                session,
                job_id=job_id,
                attempt_id=attempt_id,
                content_id=content_id,
                result=results_repo.ResultInput("extractor-2", 2, '{"title":"x"}'),
                accepted_at=datetime(2026, 1, 2, tzinfo=UTC),
            )
            current_decision = await decisions_repo.current(session, job_id)
            current_result = await results_repo.current(session, job_id)
            decisions = list(
                (await session.execute(select(models.JobDecision))).scalars()
            )
            results = list((await session.execute(select(models.JobResult))).scalars())
            assert current_decision is not None
            assert current_result is not None
            return (
                current_decision.decision,
                current_result.extractor_version,
                sum(row.is_current for row in decisions),
                sum(row.is_current for row in results),
            )

    assert run_async(scenario()) == ("eligible", "extractor-2", 1, 1)


def test_reap_releases_only_attempts_older_than_bound(tmp_path: Path) -> None:
    async def scenario() -> tuple[int, str, str | None, str | None]:
        factory = await _fresh_database(tmp_path / "reap.db")
        job_id = await _seed_job(factory)
        async with factory() as session:
            await jobs_repo.claim(session, job_id)
            await runs_repo.start_run(
                session,
                run_id="run-1",
                selection="all",
                model="gpt-4.1-mini",
                started_at=datetime(2026, 1, 1),
            )
            old_attempt = await attempts_repo.start(
                session,
                job_id=job_id,
                run_id="run-1",
                trigger="scheduled",
                reason=None,
                model="gpt-4.1-mini",
                policy_version="policy-1",
                extractor_version="extractor-1",
                started_at=datetime(2026, 1, 1),
            )
            await session.execute(
                update(models.Job)
                .where(models.Job.id == job_id)
                .values(current_attempt_id=old_attempt)
            )
            await session.commit()
            recent_attempt = await attempts_repo.start(
                session,
                job_id=job_id,
                run_id="run-1",
                trigger="reprocess",
                reason="test",
                model="gpt-4.1-mini",
                policy_version="policy-1",
                extractor_version="extractor-1",
                started_at=datetime(2026, 1, 3),
            )
            reaped = await runs_repo.reap_stale(
                session,
                older_than=datetime(2026, 1, 2, tzinfo=UTC),
                finished_at=datetime(2026, 1, 4, tzinfo=UTC),
            )
            job = await jobs_repo.get(session, job_id)
            old = await session.get(models.JobAttempt, old_attempt)
            recent = await session.get(models.JobAttempt, recent_attempt)
            assert job is not None
            assert old is not None
            assert recent is not None
            return reaped, job.status, old.error_category, recent.error_category

    assert run_async(scenario()) == (1, "retry", "interrupted", None)


def test_observation_planning_reprocess_and_show_reads(tmp_path: Path) -> None:
    async def scenario() -> None:
        factory = await _fresh_database(tmp_path / "operator-apis.db")
        job_id = await _seed_job(factory)
        attempt_id, content_id = await _seed_attempt_and_content(factory, job_id)
        async with factory() as session:
            due = await jobs_repo.list_work(
                session,
                {("acme", "https://acme.example/jobs/1")},
                datetime(2026, 1, 2),
                None,
            )
            assert [job.id for job in due] == [job_id]
        async with factory() as session:
            await jobs_repo.claim(session, job_id)
        async with factory() as session:
            await jobs_repo.release(
                session,
                job_id,
                status="retry",
                next_phase="eligibility",
                next_attempt_after=datetime(2026, 1, 3),
            )

        async with factory() as session:
            assert not await jobs_repo.list_work(
                session,
                {("acme", "https://acme.example/jobs/1")},
                datetime(2026, 1, 2),
                None,
            )
            selected = await jobs_repo.list_by_ids(session, [job_id])
            by_status = await jobs_repo.list_by_status(session, "acme", "retry")
            by_url = await jobs_repo.list_by_url(session, "https://acme.example/jobs/1")
            sources = await jobs_repo.list_sources(session, job_id)
            contents = await contents_repo.history(session, job_id)
            attempts = await attempts_repo.history(session, job_id)
        async with factory() as session:
            prepared = await jobs_repo.prepare_reprocess(
                session, [job_id, job_id], from_phase="fetch"
            )
        assert [job.id for job in selected] == [job_id]
        assert [job.id for job in by_status] == [job_id]
        assert [job.id for job in by_url] == [job_id]
        assert [source.url for source in sources] == ["https://acme.example/jobs/1"]
        assert [content.id for content in contents] == [content_id]
        assert [attempt.id for attempt in attempts] == [attempt_id]
        assert prepared == (job_id,)
        async with factory() as session:
            prepared_job = await jobs_repo.get(session, job_id)
            assert prepared_job is not None
            assert prepared_job.status == "retry"
            assert prepared_job.next_phase == "fetch"

    run_async(scenario())


def test_large_snapshot_plans_without_expression_depth_failure(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[int, dict[str, int]]:
        factory = await _fresh_database(tmp_path / "large-snapshot.db")
        await _seed_company(factory)
        observations = [
            (f"key-{index}", f"https://acme.example/jobs/{index}")
            for index in range(1504)
        ]
        async with factory() as session:
            await jobs_repo.observe(
                session,
                "acme",
                observations,
                datetime(2026, 1, 1, tzinfo=UTC),
            )
        snapshot = {("acme", key) for key, _ in observations}
        async with factory() as session:
            work = await jobs_repo.list_work(
                session, snapshot, datetime(2026, 1, 2, tzinfo=UTC), None
            )
            counts = await jobs_repo.disposition_counts(session, snapshot)
        return len(work), counts

    assert run_async(scenario()) == (1504, {"new": 1504})


def test_reaped_job_is_immediately_due(tmp_path: Path) -> None:
    async def scenario() -> tuple[int, list[int]]:
        factory = await _fresh_database(tmp_path / "reap-due.db")
        job_id = await _seed_job(factory)
        async with factory() as session:
            await jobs_repo.claim(session, job_id)
        async with factory() as session:
            await runs_repo.start_run(
                session,
                run_id="run-1",
                selection="all",
                model="gpt-4.1-mini",
                started_at=datetime(2026, 1, 1),
            )
        async with factory() as session:
            attempt_id = await attempts_repo.start(
                session,
                job_id=job_id,
                run_id="run-1",
                trigger="scheduled",
                reason=None,
                model="gpt-4.1-mini",
                policy_version="policy-1",
                extractor_version="extractor-1",
                started_at=datetime(2026, 1, 1),
            )
            await session.execute(
                update(models.Job)
                .where(models.Job.id == job_id)
                .values(current_attempt_id=attempt_id)
            )
            await session.commit()

        reaped_at = datetime(2026, 1, 3, tzinfo=UTC)
        async with factory() as session:
            reaped = await runs_repo.reap_stale(
                session,
                older_than=datetime(2026, 1, 2, tzinfo=UTC),
                finished_at=reaped_at,
            )
        async with factory() as session:
            work = await jobs_repo.list_work(
                session,
                {("acme", "https://acme.example/jobs/1")},
                reaped_at,
                None,
            )
        return reaped, [job.id for job in work]

    assert run_async(scenario()) == (1, [1])


def test_duplicate_lookup_uses_prior_canonical_job_and_transfers_sources(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[int | None, list[str], int | None]:
        factory = await _fresh_database(tmp_path / "canonical-duplicate.db")
        await _seed_company(factory)
        async with factory() as session:
            await jobs_repo.observe(
                session,
                "acme",
                [
                    ("key-1", "https://acme.example/jobs/1"),
                    ("key-2", "https://acme.example/jobs/2"),
                    ("key-3", "https://acme.example/jobs/3"),
                ],
                datetime(2026, 1, 1, tzinfo=UTC),
            )
            jobs = await jobs_repo.list_by_ids(session, [1, 2, 3])
        for job in jobs:
            async with factory() as session:
                await contents_repo.store(
                    session,
                    job.id,
                    contents_repo.AcquiredContent(
                        content_hash=f"hash-{job.id}",
                        fingerprint="same",
                        requisition_id="REQ-SAME",
                        resolved_url=f"https://acme.example/jobs/{job.id}",
                        http_status=200,
                        method="main_content",
                        title="Engineer",
                        text="Posting text",
                        jsonld_json=None,
                        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
                    ),
                )
        async with factory() as session:
            await jobs_repo.mark_merged(session, 2, 1)
        async with factory() as session:
            duplicate = await jobs_repo.find_by_fingerprint(
                session, "acme", "same", excluding_job_id=3
            )
            sources = await jobs_repo.list_sources(session, 1)
            merged = await jobs_repo.get(session, 2)
        return (
            duplicate.id if duplicate is not None else None,
            [source.url for source in sources],
            merged.merged_into_id if merged is not None else None,
        )

    assert run_async(scenario()) == (
        1,
        ["https://acme.example/jobs/1", "https://acme.example/jobs/2"],
        1,
    )


def test_stale_attempt_cannot_release_new_owner(tmp_path: Path) -> None:
    async def scenario() -> tuple[bool, str, int | None]:
        factory = await _fresh_database(tmp_path / "stale-owner.db")
        job_id = await _seed_job(factory)
        async with factory() as session:
            await runs_repo.start_run(
                session,
                run_id="run-1",
                selection="all",
                model="test",
                started_at=datetime(2026, 1, 1),
            )
            first = await attempts_repo.claim_and_start(
                session,
                job_id=job_id,
                run_id="run-1",
                trigger="scheduled",
                reason=None,
                model="test",
                policy_version="policy",
                extractor_version="extractor",
                started_at=datetime(2026, 1, 1),
            )
            assert first is not None
        async with factory() as session:
            await runs_repo.reap_stale(
                session,
                older_than=datetime(2026, 1, 2),
                finished_at=datetime(2026, 1, 3),
            )
        async with factory() as session:
            second = await attempts_repo.claim_and_start(
                session,
                job_id=job_id,
                run_id="run-1",
                trigger="scheduled",
                reason=None,
                model="test",
                policy_version="policy",
                extractor_version="extractor",
                started_at=datetime(2026, 1, 3),
            )
            assert second is not None
        async with factory() as session:
            released = await jobs_repo.release(
                session,
                job_id,
                status="failed",
                next_phase="fetch",
                next_attempt_after=None,
                attempt_id=first,
            )
        async with factory() as session:
            job = await jobs_repo.get(session, job_id)
        assert job is not None
        return released, job.status, job.current_attempt_id

    assert run_async(scenario()) == (False, "in_progress", 2)


def test_eligibility_reprocess_uses_current_content_checkpoint(tmp_path: Path) -> None:
    async def scenario() -> int | None:
        factory = await _fresh_database(tmp_path / "reprocess-current-content.db")
        job_id = await _seed_job(factory)
        content_ids = []
        for index in (1, 2):
            async with factory() as session:
                content_ids.append(
                    await contents_repo.store(
                        session,
                        job_id,
                        contents_repo.AcquiredContent(
                            content_hash=f"hash-{index}",
                            fingerprint=f"fingerprint-{index}",
                            requisition_id=f"REQ-{index}",
                            resolved_url=f"https://acme.example/jobs/{index}",
                            http_status=200,
                            method="main_content",
                            title="Engineer",
                            text=f"Posting text {index}",
                            jsonld_json=None,
                            retrieved_at=datetime(2026, 1, index),
                        ),
                    )
                )
        async with factory() as session, session.begin():
            await session.execute(
                update(models.Job)
                .where(models.Job.id == job_id)
                .values(current_content_id=content_ids[0])
            )
        async with factory() as session:
            await jobs_repo.prepare_reprocess(
                session, [job_id], from_phase="eligibility"
            )
        async with factory() as session:
            prepared = await jobs_repo.get(session, job_id)
        assert prepared is not None
        return cast(int | None, prepared.current_content_id)

    assert run_async(scenario()) == 1


def test_reprocess_is_atomic_due_and_resets_retry_budget(tmp_path: Path) -> None:
    async def scenario() -> tuple[int, datetime | None, str]:
        factory = await _fresh_database(tmp_path / "reprocess-safe.db")
        job_id = await _seed_job(factory)
        async with factory() as session:
            with pytest.raises(ValueError, match="no content checkpoint"):
                await jobs_repo.prepare_reprocess(
                    session, [job_id], from_phase="eligibility"
                )
        async with factory() as session:
            untouched = await jobs_repo.get(session, job_id)
        assert untouched is not None
        assert untouched.status == "new"
        async with factory() as session:
            await jobs_repo.claim(session, job_id)
        async with factory() as session:
            with pytest.raises(ValueError, match="active job IDs"):
                await jobs_repo.prepare_reprocess(session, [job_id], from_phase="fetch")
        async with factory() as session:
            await jobs_repo.release(
                session,
                job_id,
                status="failed",
                next_phase="fetch",
                next_attempt_after=None,
            )
        async with factory() as session:
            await jobs_repo.prepare_reprocess(session, [job_id], from_phase="fetch")
        async with factory() as session:
            prepared = await jobs_repo.get(session, job_id)
        assert prepared is not None
        return (
            prepared.attempt_count,
            prepared.next_attempt_after,
            prepared.status,
        )

    attempt_count, retry_at, status = run_async(scenario())
    assert attempt_count == 0
    assert retry_at is not None
    assert status == "retry"


@pytest.mark.parametrize(
    ("first_requisition", "second_requisition"),
    [("REQ-SAME", "REQ-SAME"), ("REQ-ONE", "REQ-TWO")],
)
def test_duplicate_merge_always_uses_lower_canonical(
    tmp_path: Path,
    first_requisition: str,
    second_requisition: str,
) -> None:
    async def scenario() -> tuple[int, str, int | None]:
        factory = await _fresh_database(tmp_path / "deterministic-merge.db")
        await _seed_company(factory)
        async with factory() as session:
            await jobs_repo.observe(
                session,
                "acme",
                [
                    ("key-1", "https://acme.example/1"),
                    ("key-2", "https://acme.example/2"),
                ],
                datetime(2026, 1, 1),
            )
        for job_id in (2, 1):
            async with factory() as session:
                await contents_repo.store(
                    session,
                    job_id,
                    contents_repo.AcquiredContent(
                        content_hash=f"hash-{job_id}",
                        fingerprint="same",
                        requisition_id=(
                            first_requisition if job_id == 1 else second_requisition
                        ),
                        resolved_url=f"https://acme.example/{job_id}",
                        http_status=200,
                        method="main_content",
                        title="Engineer",
                        text="Posting text",
                        jsonld_json=None,
                        retrieved_at=datetime(2026, 1, 1),
                    ),
                )
        async with factory() as session:
            canonical = await jobs_repo.merge_duplicate(
                session,
                job_id=1,
                company_slug="acme",
                requisition_id=first_requisition,
                fingerprint="same",
            )
        async with factory() as session:
            first = await jobs_repo.get(session, 1)
            second = await jobs_repo.get(session, 2)
        assert canonical is not None
        assert first is not None
        assert second is not None
        with pytest.raises(ValueError, match="lower job ID"):
            async with factory() as session:
                await jobs_repo.mark_merged(session, 1, 2)
        return canonical, second.status, second.merged_into_id

    assert run_async(scenario()) == (1, "merged", 1)


def test_restrict_blocks_deleting_company_with_jobs(tmp_path: Path) -> None:
    async def scenario() -> None:
        factory = await _fresh_database(tmp_path / "restrict.db")
        await _seed_job(factory)
        async with factory() as session, session.begin():
            await session.execute(
                delete(discovery_models.Company).where(
                    discovery_models.Company.slug == "acme"
                )
            )

    with pytest.raises(IntegrityError):
        run_async(scenario())
