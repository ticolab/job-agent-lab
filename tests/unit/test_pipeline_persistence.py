"""Focused repository tests against a real SQLite database file."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import job_pipeline.persistence.attempts_repo as attempts_repo
import job_pipeline.persistence.contents_repo as contents_repo
import job_pipeline.persistence.decisions_repo as decisions_repo
import job_pipeline.persistence.finalize as finalize
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
            stage="eligibility",
            trigger="scheduled",
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
                return cast(
                    bool, await jobs_repo.claim(session, job_id, stage="eligibility")
                )

        first, second = await asyncio.gather(claimant(), claimant())
        return first, second

    assert sorted(run_async(scenario())) == [False, True]


def test_claim_rechecks_retry_due_time(tmp_path: Path) -> None:
    async def scenario() -> bool:
        factory = await _fresh_database(tmp_path / "retry-due-claim.db")
        job_id = await _seed_job(factory)
        async with factory() as session:
            await jobs_repo.claim(session, job_id, stage="eligibility")
        async with factory() as session:
            await jobs_repo.release(
                session,
                job_id,
                status="retry",
                next_phase="fetch",
                next_attempt_after=datetime(2099, 1, 1),
            )
        async with factory() as session:
            return cast(
                bool, await jobs_repo.claim(session, job_id, stage="eligibility")
            )

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
            await jobs_repo.claim(session, job_id, stage="eligibility")
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
            await jobs_repo.claim(session, job_id, stage="eligibility")
            await runs_repo.start_run(
                session,
                stage="eligibility",
                trigger="scheduled",
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
                stage="eligibility",
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
                stage="eligibility",
            )
            assert [job.id for job in due] == [job_id]
        async with factory() as session:
            await jobs_repo.claim(session, job_id, stage="eligibility")
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
                stage="eligibility",
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
                session,
                snapshot,
                datetime(2026, 1, 2, tzinfo=UTC),
                None,
                stage="eligibility",
            )
            counts = await jobs_repo.stage_dispositions(
                session, snapshot, datetime(2026, 1, 2)
            )
        return len(work), counts

    assert run_async(scenario()) == (1504, {"new": 1504})


def test_reaped_job_is_immediately_due(tmp_path: Path) -> None:
    async def scenario() -> tuple[int, list[int]]:
        factory = await _fresh_database(tmp_path / "reap-due.db")
        job_id = await _seed_job(factory)
        async with factory() as session:
            await jobs_repo.claim(session, job_id, stage="eligibility")
        async with factory() as session:
            await runs_repo.start_run(
                session,
                stage="eligibility",
                trigger="scheduled",
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
                stage="eligibility",
                older_than=datetime(2026, 1, 2, tzinfo=UTC),
                finished_at=reaped_at,
            )
        async with factory() as session:
            work = await jobs_repo.list_work(
                session,
                {("acme", "https://acme.example/jobs/1")},
                reaped_at,
                None,
                stage="eligibility",
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
                stage="eligibility",
                trigger="scheduled",
                run_id="run-1",
                selection="all",
                model="test",
                started_at=datetime(2026, 1, 1),
            )
            first = await attempts_repo.claim_and_start(
                session,
                stage="eligibility",
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
                stage="eligibility",
                older_than=datetime(2026, 1, 2),
                finished_at=datetime(2026, 1, 3),
            )
        async with factory() as session:
            second = await attempts_repo.claim_and_start(
                session,
                stage="eligibility",
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
            await jobs_repo.claim(session, job_id, stage="eligibility")
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


async def _store_content(
    factory: async_sessionmaker[AsyncSession],
    job_id: int,
    *,
    title: str,
    fingerprint: str,
    requisition_id: str | None,
) -> None:
    async with factory() as session:
        await contents_repo.store(
            session,
            job_id,
            contents_repo.AcquiredContent(
                content_hash=f"hash-{job_id}",
                fingerprint=fingerprint,
                requisition_id=requisition_id,
                resolved_url=f"https://acme.example/jobs/{job_id}",
                http_status=200,
                method="main_content",
                title=title,
                text=f"Posting text {job_id}",
                jsonld_json=None,
                retrieved_at=datetime(2026, 1, 1),
            ),
        )


async def _three_jobs(factory: async_sessionmaker[AsyncSession]) -> None:
    await _seed_company(factory)
    async with factory() as session:
        await jobs_repo.observe(
            session,
            "acme",
            [
                (f"key-{index}", f"https://acme.example/jobs/{index}")
                for index in (1, 2, 3)
            ],
            datetime(2026, 1, 1),
        )


def test_shared_requisition_with_different_titles_never_merges(
    tmp_path: Path,
) -> None:
    """A board stamping every posting with one identifier must stay many jobs."""

    async def scenario() -> tuple[int | None, list[str]]:
        factory = await _fresh_database(tmp_path / "constant-identifier.db")
        await _three_jobs(factory)
        for job_id, title in ((1, "Engineer"), (2, "Analyst"), (3, "Designer")):
            await _store_content(
                factory,
                job_id,
                title=title,
                fingerprint=f"fingerprint-{job_id}",
                requisition_id="ACME",
            )
        async with factory() as session:
            canonical = await jobs_repo.merge_duplicate(
                session,
                job_id=3,
                company_slug="acme",
                requisition_id="ACME",
                fingerprint="fingerprint-3",
            )
        async with factory() as session:
            jobs = await jobs_repo.list_by_ids(session, [1, 2, 3])
        return canonical, [job.status for job in jobs]

    assert run_async(scenario()) == (3, ["new", "new", "new"])


def test_requisition_shared_by_several_jobs_is_not_identifying(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[int | None, list[str]]:
        factory = await _fresh_database(tmp_path / "ambiguous-identifier.db")
        await _three_jobs(factory)
        for job_id in (1, 2, 3):
            await _store_content(
                factory,
                job_id,
                title="Engineer",
                fingerprint=f"fingerprint-{job_id}",
                requisition_id="ACME",
            )
        async with factory() as session:
            canonical = await jobs_repo.merge_duplicate(
                session,
                job_id=3,
                company_slug="acme",
                requisition_id="ACME",
                fingerprint="fingerprint-3",
            )
        async with factory() as session:
            jobs = await jobs_repo.list_by_ids(session, [1, 2, 3])
        return canonical, [job.status for job in jobs]

    assert run_async(scenario()) == (3, ["new", "new", "new"])


def test_matching_requisition_and_title_merges_into_the_lower_id(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[int | None, str, int | None, list[str]]:
        factory = await _fresh_database(tmp_path / "requisition-merge.db")
        await _three_jobs(factory)
        await _store_content(
            factory, 1, title="Engineer", fingerprint="fp-1", requisition_id="R-1"
        )
        await _store_content(
            factory, 3, title="engineer ", fingerprint="fp-3", requisition_id="R-1"
        )
        async with factory() as session:
            canonical = await jobs_repo.merge_duplicate(
                session,
                job_id=3,
                company_slug="acme",
                requisition_id="R-1",
                fingerprint="fp-3",
            )
        async with factory() as session:
            merged = await jobs_repo.get(session, 3)
            sources = await jobs_repo.list_sources(session, 1)
        assert merged is not None
        return (
            canonical,
            merged.status,
            merged.merged_into_id,
            [source.url for source in sources],
        )

    assert run_async(scenario()) == (
        1,
        "merged",
        1,
        ["https://acme.example/jobs/1", "https://acme.example/jobs/3"],
    )


def test_duplicate_owned_by_another_worker_is_left_alone(tmp_path: Path) -> None:
    async def scenario() -> tuple[int | None, str, int | None, list[str]]:
        factory = await _fresh_database(tmp_path / "in-progress-duplicate.db")
        await _three_jobs(factory)
        async with factory() as session:
            await runs_repo.start_run(
                session,
                stage="eligibility",
                trigger="scheduled",
                run_id="run-1",
                selection="acme",
                model="test",
                started_at=datetime(2026, 1, 1),
            )
        async with factory() as session:
            claimed = await attempts_repo.claim_and_start(
                session,
                stage="eligibility",
                job_id=2,
                run_id="run-1",
                trigger="scheduled",
                reason=None,
                model="test",
                policy_version="policy",
                extractor_version="extractor",
                started_at=datetime(2026, 1, 1),
            )
        assert claimed is not None
        for job_id in (1, 2):
            await _store_content(
                factory,
                job_id,
                title="Engineer",
                fingerprint="same",
                requisition_id=None,
            )
        async with factory() as session:
            canonical = await jobs_repo.merge_duplicate(
                session,
                job_id=1,
                company_slug="acme",
                requisition_id=None,
                fingerprint="same",
            )
        async with factory() as session:
            other = await jobs_repo.get(session, 2)
            sources = await jobs_repo.list_sources(session, 1)
        assert other is not None
        return canonical, other.status, other.merged_into_id, [s.url for s in sources]

    assert run_async(scenario()) == (
        1,
        "in_progress",
        None,
        ["https://acme.example/jobs/1"],
    )


@pytest.mark.parametrize("stage", ["eligibility", "extraction"])
@pytest.mark.parametrize("trigger", ["scheduled", "reprocess"])
def test_stage_selection_matrix(
    tmp_path: Path, stage: models.Stage, trigger: str
) -> None:
    async def scenario() -> None:
        factory = await _fresh_database(tmp_path / "matrix.db")
        await _seed_company(factory)
        now = datetime(2026, 1, 2)
        expected: set[int] = set()
        async with factory() as session, session.begin():
            for status in models.JOB_STATUSES:
                for phase in models.PHASES:
                    if status == "ready_for_extraction" and phase != "extraction":
                        continue
                    for due in (False, True):
                        for requested in (False, True):
                            job = models.Job(
                                company_slug="acme",
                                url_key=str(len(session.new)),
                                status=status,
                                next_phase=phase,
                                attempt_count=2,
                                next_attempt_after=(
                                    now + timedelta(days=-1 if due else 1)
                                    if status == "retry"
                                    else None
                                ),
                                reprocess_requested=requested,
                                first_seen_at=now,
                                last_seen_at=now,
                                updated_at=now,
                            )
                            job.url_key = f"{status}-{phase}-{due}-{requested}"
                            session.add(job)
                            await session.flush()
                            retry = (
                                status == "retry"
                                and due
                                and phase in models.STAGE_PHASES[stage]
                            )
                            waiting = status == (
                                "new"
                                if stage == "eligibility"
                                else "ready_for_extraction"
                            )
                            selected = (
                                requested and retry
                                if trigger == "reprocess"
                                else not requested and (waiting or retry)
                            )
                            if selected:
                                expected.add(job.id)
            session.add(
                models.PipelineRun(
                    id="matrix",
                    stage=stage,
                    trigger=trigger,
                    selection="all",
                    model="test",
                    started_at=now,
                )
            )
        async with factory() as session:
            rows = await jobs_repo.list_by_companies(session, ["acme"])
            snapshot = {(row.company_slug, row.url_key) for row in rows}
            planned = await jobs_repo.list_work(
                session,
                snapshot,
                now,
                None,
                stage=stage,
                reprocess=trigger == "reprocess",
            )
            assert {row.id for row in planned} == expected
        claimed: set[int] = set()
        for row in rows:
            async with factory() as session:
                attempt = await attempts_repo.claim_and_start(
                    session,
                    stage=stage,
                    job_id=row.id,
                    run_id="matrix",
                    trigger=trigger,
                    reason=None,
                    model="test",
                    policy_version="p",
                    extractor_version="e",
                    started_at=now,
                )
                if attempt is not None:
                    claimed.add(row.id)
        assert claimed == expected

    run_async(scenario())


def test_reap_is_scoped_by_stage(tmp_path: Path) -> None:
    async def scenario() -> None:
        factory = await _fresh_database(tmp_path / "stage-reap.db")
        await _three_jobs(factory)
        now = datetime(2026, 1, 1)
        async with factory() as session, session.begin():
            session.add(
                models.PipelineRun(
                    id="old",
                    stage="combined",
                    trigger="scheduled",
                    selection="all",
                    model="test",
                    started_at=now,
                )
            )
            await session.flush()
            for job_id, phase in ((1, "fetch"), (2, "extraction")):
                attempt = models.JobAttempt(
                    job_id=job_id,
                    run_id="old",
                    trigger="scheduled",
                    model="test",
                    policy_version="p",
                    extractor_version="e",
                    started_at=now,
                )
                session.add(attempt)
                await session.flush()
                await session.execute(
                    update(models.Job)
                    .where(models.Job.id == job_id)
                    .values(
                        status="in_progress",
                        next_phase=phase,
                        current_attempt_id=attempt.id,
                    )
                )
        for stage, reaped_id, untouched_id in (
            ("eligibility", 1, 2),
            ("extraction", 2, 3),
        ):
            async with factory() as session:
                assert (
                    await runs_repo.reap_stale(
                        session,
                        stage=cast(models.Stage, stage),
                        older_than=now + timedelta(days=1),
                        finished_at=now + timedelta(days=2),
                    )
                    == 1
                )
            async with factory() as session:
                job = await jobs_repo.get(session, reaped_id)
                untouched = await jobs_repo.get(session, untouched_id)
                assert job is not None and job.status == "retry"
                assert untouched is not None
                assert untouched.status == (
                    "in_progress" if untouched_id == 2 else "new"
                )

    run_async(scenario())


@pytest.mark.parametrize(
    "variant", ["new", "checkpoint", "mismatch", "lost", "finished", "wrong_content"]
)
def test_atomic_hand_off(tmp_path: Path, variant: str) -> None:
    async def scenario() -> None:
        factory = await _fresh_database(tmp_path / "handoff.db")
        job_id = await _seed_job(factory)
        attempt_id, content_id = await _seed_attempt_and_content(factory, job_id)
        decision = decisions_repo.DecisionInput("eligible", "location", "[]", "p")
        async with factory() as session, session.begin():
            await session.execute(
                update(models.Job)
                .where(models.Job.id == job_id)
                .values(
                    status="in_progress",
                    next_phase="eligibility",
                    attempt_count=3,
                    current_attempt_id=attempt_id,
                    current_content_id=content_id,
                )
            )
        if variant in {"checkpoint", "mismatch", "new"}:
            async with factory() as session:
                await decisions_repo.accept(
                    session,
                    job_id=job_id,
                    attempt_id=attempt_id,
                    content_id=content_id,
                    decision=decision
                    if variant == "checkpoint"
                    else decisions_repo.DecisionInput("unknown", "old", "[]", "p"),
                    decided_at=datetime(2026, 1, 1),
                )
        if variant == "finished":
            async with factory() as session, session.begin():
                await session.execute(
                    update(models.JobAttempt).values(
                        finished_at=datetime(2026, 1, 1), outcome="failed"
                    )
                )

        async def hand_off() -> int | None:
            async with factory() as session:
                return await decisions_repo.hand_off_to_extraction(
                    session,
                    job_id=job_id,
                    attempt_id=attempt_id + 1 if variant == "lost" else attempt_id,
                    content_id=content_id + 1
                    if variant == "wrong_content"
                    else content_id,
                    decision=None
                    if variant in {"checkpoint", "mismatch"}
                    else decision,
                    decided_at=datetime(2026, 1, 2, tzinfo=UTC),
                    prompt_tokens=11,
                    completion_tokens=7,
                )

        if variant in {"mismatch", "finished"}:
            with pytest.raises(decisions_repo.CheckpointError):
                await hand_off()
        else:
            result = await hand_off()
            assert (result is None) == (variant in {"lost", "wrong_content"})
        success = variant in {"new", "checkpoint"}
        async with factory() as session:
            job = await session.get(models.Job, job_id)
            attempt = await session.get(models.JobAttempt, attempt_id)
            decisions = tuple(
                (await session.execute(select(models.JobDecision))).scalars()
            )
            assert job is not None and attempt is not None
            assert job.status == ("ready_for_extraction" if success else "in_progress")
            assert job.attempt_count == (0 if success else 3)
            assert job.current_attempt_id == (None if success else attempt_id)
            assert job.next_phase == ("extraction" if success else "eligibility")
            assert job.next_attempt_after is None
            if success:
                assert attempt.outcome == "ready_for_extraction"
                assert attempt.phase_reached == "eligibility"
                assert attempt.finished_at == datetime(2026, 1, 2)
                assert (attempt.prompt_tokens, attempt.completion_tokens) == (11, 7)
                assert sum(row.is_current for row in decisions) == 1
                assert len(decisions) == (2 if variant == "new" else 1)
            else:
                assert attempt.outcome == ("failed" if variant == "finished" else None)
                assert len(decisions) == (1 if variant == "mismatch" else 0)

    run_async(scenario())


def test_canonical_keys_are_read_only_and_merge_aware(tmp_path: Path) -> None:
    async def scenario() -> None:
        factory = await _fresh_database(tmp_path / "canonical.db")
        await _three_jobs(factory)
        async with factory() as session, session.begin():
            await session.execute(
                update(models.Job)
                .where(models.Job.id == 3)
                .values(status="merged", merged_into_id=2)
            )
            await session.execute(
                update(models.Job)
                .where(models.Job.id == 2)
                .values(status="merged", merged_into_id=1)
            )
        async with factory() as session:
            before = {
                table.name: (await session.execute(select(table))).all()
                for table in models.Base.metadata.sorted_tables
            }
            assert await jobs_repo.canonical_keys(
                session, "acme", {"key-3", "missing"}
            ) == frozenset({"key-1"})
            assert (
                await jobs_repo.canonical_keys(session, "other", {"key-3"})
                == frozenset()
            )
            summary = await jobs_repo.stage_dispositions(
                session, {("acme", "key-1")}, datetime(2026, 1, 1)
            )
            assert summary == {"new": 1}
            for table in models.Base.metadata.sorted_tables:
                assert (await session.execute(select(table))).all() == before[
                    table.name
                ]
        async with factory() as session:
            observed = await jobs_repo.observe(
                session, "acme", [("key-3", "alias")], datetime(2026, 1, 2)
            )
            assert observed.snapshot_keys == frozenset({"key-1"})

    run_async(scenario())


@pytest.mark.parametrize("broken", ["cycle", "missing"])
def test_canonical_keys_match_observation_errors(tmp_path: Path, broken: str) -> None:
    async def scenario() -> None:
        factory = await _fresh_database(tmp_path / "broken-merge.db")
        await _three_jobs(factory)
        await _seed_company(factory, "other")
        async with factory() as session:
            await jobs_repo.observe(
                session,
                "other",
                [("other-key", "https://other.example/1")],
                datetime(2026, 1, 1),
            )
        async with factory() as session, session.begin():
            await session.execute(
                update(models.Job)
                .where(models.Job.id == 2)
                .values(status="merged", merged_into_id=3 if broken == "cycle" else 4)
            )
            if broken == "cycle":
                await session.execute(
                    update(models.Job)
                    .where(models.Job.id == 3)
                    .values(status="merged", merged_into_id=2)
                )
        message = (
            "merge cycle detected at job 2"
            if broken == "cycle"
            else "merged job 2 has missing canonical job"
        )
        async with factory() as session:
            with pytest.raises(RuntimeError, match=message):
                await jobs_repo.canonical_keys(session, "acme", {"key-2"})
        async with factory() as session:
            with pytest.raises(RuntimeError, match=message):
                await jobs_repo.observe(
                    session, "acme", [("key-2", "alias")], datetime(2026, 1, 2)
                )

    run_async(scenario())


def test_atomic_claim_has_one_winner(tmp_path: Path) -> None:
    async def scenario() -> None:
        factory = await _fresh_database(tmp_path / "atomic-claim.db")
        job_id = await _seed_job(factory)
        async with factory() as session:
            await runs_repo.start_run(
                session,
                run_id="race",
                stage="eligibility",
                trigger="scheduled",
                selection="all",
                model="test",
                started_at=datetime(2026, 1, 1),
            )

        async def claimant() -> int | None:
            async with factory() as session:
                return await attempts_repo.claim_and_start(
                    session,
                    stage="eligibility",
                    job_id=job_id,
                    run_id="race",
                    trigger="scheduled",
                    reason=None,
                    model="test",
                    policy_version="p",
                    extractor_version="e",
                    started_at=datetime(2026, 1, 1),
                )

        winners = await asyncio.gather(claimant(), claimant())
        assert sum(result is not None for result in winners) == 1
        async with factory() as session:
            attempts = await attempts_repo.history(session, job_id)
            job = await jobs_repo.get(session, job_id)
            assert len(attempts) == 1
            assert job is not None and job.current_attempt_id == attempts[0].id
            assert job.attempt_count == 1

    run_async(scenario())


def test_stage_dispositions(tmp_path: Path) -> None:
    async def scenario() -> None:
        factory = await _fresh_database(tmp_path / "dispositions.db")
        await _seed_company(factory)
        now = datetime(2026, 1, 2)
        expected = {status: 1 for status in models.JOB_STATUSES if status != "retry"}
        async with factory() as session, session.begin():
            for status in expected:
                session.add(
                    models.Job(
                        company_slug="acme",
                        url_key=status,
                        status=status,
                        next_phase="extraction"
                        if status == "ready_for_extraction"
                        else "fetch",
                        first_seen_at=now,
                        last_seen_at=now,
                        updated_at=now,
                    )
                )
            for phase in models.PHASES:
                for due in (False, True):
                    key = f"{phase}-{due}"
                    session.add(
                        models.Job(
                            company_slug="acme",
                            url_key=key,
                            status="retry",
                            next_phase=phase,
                            next_attempt_after=now + timedelta(days=-1 if due else 1),
                            first_seen_at=now,
                            last_seen_at=now,
                            updated_at=now,
                        )
                    )
                    stage = "extraction" if phase == "extraction" else "eligibility"
                    timing = "due" if due else "waiting"
                    name = f"{stage}_retry_{timing}"
                    expected[name] = expected.get(name, 0) + 1
        async with factory() as session:
            jobs = await jobs_repo.list_by_companies(session, ["acme"])
            assert (
                await jobs_repo.stage_dispositions(
                    session, {("acme", job.url_key) for job in jobs}, now
                )
                == expected
            )

    run_async(scenario())


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


async def _finalization_claim(
    factory: async_sessionmaker[AsyncSession], job_id: int
) -> int:
    async with factory() as session:
        attempt_id = await attempts_repo.claim_and_start(
            session,
            job_id=job_id,
            stage="eligibility",
            run_id="finalization",
            trigger="scheduled",
            reason=None,
            model="test",
            policy_version="p",
            extractor_version="e",
            started_at=datetime(2026, 1, 4),
        )
    assert attempt_id is not None
    return attempt_id


async def _finalization_setup(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[int, int]:
    await _three_jobs(factory)
    async with factory() as session:
        await runs_repo.start_run(
            session,
            run_id="finalization",
            stage="eligibility",
            trigger="scheduled",
            selection="all",
            model="test",
            started_at=datetime(2026, 1, 1),
        )
    await _store_content(
        factory, 1, title="Engineer", fingerprint="same", requisition_id=None
    )
    async with factory() as session:
        await jobs_repo.observe(
            session,
            "acme",
            [("key-2", "https://acme.example/jobs/1")],
            datetime(2026, 1, 1),
        )
    attempt_id = await _finalization_claim(factory, 2)
    async with factory() as session:
        content_id = await contents_repo.store_checkpoint(
            session,
            2,
            attempt_id,
            contents_repo.AcquiredContent(
                content_hash="owned-content",
                fingerprint="same",
                requisition_id=None,
                resolved_url="https://acme.example/jobs/2",
                http_status=200,
                method="main_content",
                title="Engineer",
                text="Posting text",
                jsonld_json=None,
                retrieved_at=datetime(2026, 1, 1),
            ),
        )
    assert content_id is not None
    async with factory() as session:
        await decisions_repo.accept(
            session,
            job_id=2,
            attempt_id=attempt_id,
            content_id=content_id,
            decision=decisions_repo.DecisionInput("unknown", "old", "[]", "old"),
            decided_at=datetime(2026, 1, 1),
        )
    async with factory() as session:
        await results_repo.accept(
            session,
            job_id=2,
            attempt_id=attempt_id,
            content_id=content_id,
            result=results_repo.ResultInput("old", 1, "{}"),
            accepted_at=datetime(2026, 1, 1),
        )
    return attempt_id, content_id


async def _finalization_snapshot(
    factory: async_sessionmaker[AsyncSession],
) -> dict[str, list[tuple[object, ...]]]:
    async with factory() as session:
        return {
            table.name: [
                tuple(row)
                for row in (
                    await session.execute(select(table).order_by(*table.primary_key))
                ).all()
            ]
            for table in models.Base.metadata.sorted_tables
        }


async def _assert_no_finalization_orphans(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        assert not (
            await session.execute(
                select(models.JobAttempt.id)
                .join(models.Job, models.Job.id == models.JobAttempt.job_id)
                .where(
                    models.JobAttempt.finished_at.is_(None),
                    or_(
                        models.Job.status != "in_progress",
                        models.Job.current_attempt_id.is_(None),
                        models.Job.current_attempt_id != models.JobAttempt.id,
                    ),
                )
            )
        ).all()


async def _finalize_repository(
    session: AsyncSession, operation: str, attempt_id: int, content_id: int
) -> int | bool | None:
    moment = datetime(2026, 1, 5, tzinfo=UTC)
    if operation == "decision":
        return await decisions_repo.accept_and_complete(
            session,
            job_id=2,
            attempt_id=attempt_id,
            content_id=content_id,
            decision=decisions_repo.DecisionInput("ineligible", "new", "[]", "new"),
            status="completed_ineligible",
            decided_at=moment,
            prompt_tokens=11,
            completion_tokens=7,
        )
    if operation == "result":
        return await results_repo.accept_and_complete(
            session,
            job_id=2,
            attempt_id=attempt_id,
            content_id=content_id,
            result=results_repo.ResultInput("new", 1, '{"new":true}'),
            accepted_at=moment,
            prompt_tokens=11,
            completion_tokens=7,
        )
    if operation == "release":
        return await jobs_repo.release(
            session,
            2,
            attempt_id=attempt_id,
            status="retry",
            next_phase="eligibility",
            next_attempt_after=moment + timedelta(days=1),
            error_category="internal",
            error_message="failure",
            prompt_tokens=11,
            completion_tokens=7,
        )
    assert operation == "merge"
    return await jobs_repo.merge_duplicate(
        session,
        job_id=2,
        attempt_id=attempt_id,
        company_slug="acme",
        requisition_id=None,
        fingerprint="same",
    )


@pytest.mark.parametrize("operation", ["decision", "result", "release", "merge"])
@pytest.mark.parametrize(
    "mode",
    ["held", "lost", "reaped", "reclaimed", "finished", "cancel", "finish_error"],
)
def test_repository_finalization_is_atomic_and_owned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, mode: str
) -> None:
    async def scenario() -> None:
        factory = await _fresh_database(tmp_path / "finalization.db")
        attempt_id, content_id = await _finalization_setup(factory)
        moment = datetime(2026, 1, 5)
        monkeypatch.setattr(jobs_repo, "utc_now", lambda: moment)
        second: int | None = None
        if mode in {"reaped", "reclaimed"}:
            async with factory() as session:
                assert (
                    await runs_repo.reap_stale(
                        session,
                        stage="eligibility",
                        older_than=datetime(2026, 1, 6),
                        finished_at=datetime(2026, 1, 3),
                    )
                    == 1
                )
            if mode == "reclaimed":
                second = await _finalization_claim(factory, 2)
        if mode == "finished":
            async with factory() as session:
                assert await attempts_repo.finish(
                    session,
                    attempt_id,
                    outcome="failed",
                    phase_reached="fetch",
                    finished_at=datetime(2026, 1, 2),
                )
        before = await _finalization_snapshot(factory)
        real_finish = finalize.finish_owned_attempt

        async def injected(
            session: AsyncSession,
            *,
            job_id: int,
            attempt_id: int,
            outcome: str,
            phase_reached: str,
            finished_at: datetime,
            error_category: str | None = None,
            error_message: str | None = None,
            prompt_tokens: int | None = None,
            completion_tokens: int | None = None,
        ) -> None:
            if mode == "cancel":
                raise asyncio.CancelledError
            await real_finish(
                session,
                job_id=job_id,
                attempt_id=attempt_id,
                outcome=outcome,
                phase_reached=phase_reached,
                finished_at=finished_at,
                error_category=error_category,
                error_message=error_message,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
            raise OperationalError("UPDATE job_attempts", {}, RuntimeError("fault"))

        if mode in {"cancel", "finish_error"}:
            monkeypatch.setattr(finalize, "finish_owned_attempt", injected)
        async with factory() as session:
            if mode in {"finished", "cancel", "finish_error"}:
                expected = (
                    finalize.CheckpointError
                    if mode == "finished"
                    else asyncio.CancelledError
                    if mode == "cancel"
                    else OperationalError
                )
                with pytest.raises(expected):
                    await _finalize_repository(
                        session, operation, attempt_id, content_id
                    )
            else:
                result = await _finalize_repository(
                    session,
                    operation,
                    attempt_id + 100 if mode == "lost" else attempt_id,
                    content_id,
                )
                if mode == "held":
                    assert result is not None and result is not False
                else:
                    assert result is (False if operation == "release" else None)
        if mode != "held":
            assert await _finalization_snapshot(factory) == before
        async with factory() as session:
            job = await jobs_repo.get(session, 2)
            attempts = await attempts_repo.history(session, 2)
            assert job is not None
            attempt = attempts[0]
            if mode == "held":
                status, phase = {
                    "decision": ("completed_ineligible", "eligibility"),
                    "result": ("completed_eligible", "extraction"),
                    "release": ("retry", "eligibility"),
                    "merge": ("merged", "fetch"),
                }[operation]
                assert job.status == attempt.outcome == status
                assert attempt.phase_reached == phase
                assert job.current_attempt_id is None
                assert job.current_content_id == content_id
                assert job.updated_at == attempt.finished_at == moment
                assert (attempt.prompt_tokens, attempt.completion_tokens) == (
                    (None, None) if operation == "merge" else (11, 7)
                )
                assert attempt.error_category == (
                    "internal" if operation == "release" else None
                )
                assert attempt.error_message == (
                    "failure" if operation == "release" else None
                )
                assert job.next_attempt_after == (
                    moment + timedelta(days=1) if operation == "release" else None
                )
                assert job.next_phase == ("fetch" if operation == "merge" else phase)
                decisions = await decisions_repo.history(session, 2)
                results = await results_repo.history(session, 2)
                assert len(decisions) == (2 if operation == "decision" else 1)
                assert len(results) == (2 if operation == "result" else 1)
                assert [row.is_current for row in decisions] == (
                    [False, True] if operation == "decision" else [True]
                )
                assert [row.is_current for row in results] == (
                    [False, True] if operation == "result" else [True]
                )
                if operation == "merge":
                    assert job.merged_into_id == 1
                    assert not await jobs_repo.list_sources(session, 2)
                    sources = await jobs_repo.list_sources(session, 1)
                    assert [row.url for row in sources] == [
                        "https://acme.example/jobs/1",
                        "https://acme.example/jobs/2",
                    ]
                else:
                    assert await jobs_repo.list_sources(session, 2)
            if mode in {"reaped", "reclaimed"}:
                assert attempt.outcome == "interrupted"
                assert attempt.finished_at == datetime(2026, 1, 3)
                assert job.current_attempt_id == second
                assert job.status == ("in_progress" if second is not None else "retry")
                if second is not None:
                    assert attempts[1].id == second
                    assert attempts[1].finished_at is None
        await _assert_no_finalization_orphans(factory)

    run_async(scenario())


@pytest.mark.parametrize("variant", ["unfinished", "finished", "other_job", "missing"])
def test_finish_owned_attempt_fields_and_outer_rollback(
    tmp_path: Path, variant: str
) -> None:
    async def scenario() -> None:
        factory = await _fresh_database(tmp_path / "finish-helper.db")
        attempt_id, _ = await _finalization_setup(factory)
        if variant == "finished":
            async with factory() as session:
                await attempts_repo.finish(
                    session,
                    attempt_id,
                    outcome="failed",
                    phase_reached="fetch",
                    finished_at=datetime(2026, 1, 2),
                )
        before = await _finalization_snapshot(factory)

        async def finish() -> None:
            async with factory() as session, session.begin():
                await session.execute(
                    update(models.Job)
                    .where(
                        models.Job.id == 2,
                        models.Job.current_attempt_id == attempt_id,
                    )
                    .values(status="failed", current_attempt_id=None)
                )
                await finalize.finish_owned_attempt(
                    session,
                    job_id=1 if variant == "other_job" else 2,
                    attempt_id=attempt_id + 100 if variant == "missing" else attempt_id,
                    outcome="failed",
                    phase_reached="extraction",
                    finished_at=datetime(2026, 1, 5, tzinfo=UTC),
                    error_category="internal",
                    error_message="all fields",
                    prompt_tokens=0,
                    completion_tokens=7,
                )

        if variant == "unfinished":
            await finish()
            async with factory() as session:
                attempt = (await attempts_repo.history(session, 2))[0]
                assert attempt.outcome == "failed"
                assert attempt.phase_reached == "extraction"
                assert attempt.finished_at == datetime(2026, 1, 5)
                assert attempt.error_category == "internal"
                assert attempt.error_message == "all fields"
                assert (attempt.prompt_tokens, attempt.completion_tokens) == (0, 7)
        else:
            with pytest.raises(
                finalize.CheckpointError,
                match=f"job {1 if variant == 'other_job' else 2} "
                "has no unfinished owned attempt",
            ):
                await finish()
            assert await _finalization_snapshot(factory) == before
        await _assert_no_finalization_orphans(factory)

    run_async(scenario())


def test_merging_younger_duplicate_preserves_canonical_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        factory = await _fresh_database(tmp_path / "canonical-lease.db")
        attempt_id, _ = await _finalization_setup(factory)
        await _store_content(
            factory, 3, title="Engineer", fingerprint="younger", requisition_id=None
        )
        async with factory() as session:
            before_job = await jobs_repo.get(session, 2)
            before_attempts = await attempts_repo.history(session, 2)

        async def forbidden(*args: object, **kwargs: object) -> None:
            pytest.fail("folding a younger duplicate must not finish the caller")

        monkeypatch.setattr(finalize, "finish_owned_attempt", forbidden)
        async with factory() as session:
            assert (
                await jobs_repo.merge_duplicate(
                    session,
                    job_id=2,
                    company_slug="acme",
                    attempt_id=attempt_id,
                    requisition_id=None,
                    fingerprint="younger",
                )
                == 2
            )
        async with factory() as session:
            assert await jobs_repo.get(session, 2) == before_job
            assert await attempts_repo.history(session, 2) == before_attempts
            younger = await jobs_repo.get(session, 3)
            assert younger is not None and younger.merged_into_id == 2
            assert not await jobs_repo.list_sources(session, 3)
            assert "https://acme.example/jobs/3" in {
                source.url for source in await jobs_repo.list_sources(session, 2)
            }
        await _assert_no_finalization_orphans(factory)

    run_async(scenario())
