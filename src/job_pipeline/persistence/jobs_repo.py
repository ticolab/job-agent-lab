"""Job observation, claiming, planning, and identity lookups."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Select, and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from job_discovery.persistence.timestamps import as_naive_utc, utc_now
from job_pipeline.persistence.models import (
    Job,
    JobContent,
    JobDecision,
    JobResult,
    JobSource,
)


@dataclass(frozen=True)
class Observation:
    url_key: str
    raw_url: str


@dataclass(frozen=True)
class ObserveSummary:
    new: int
    reseen: int
    snapshot_keys: frozenset[str] = field(default_factory=frozenset, compare=False)


@dataclass(frozen=True, slots=True)
class JobRecord:
    id: int
    company_slug: str
    url_key: str
    status: str
    next_phase: str
    attempt_count: int
    next_attempt_after: datetime | None
    current_attempt_id: int | None
    current_content_id: int | None
    reprocess_requested: bool
    merged_into_id: int | None
    first_seen_at: datetime
    last_seen_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class JobSourceRecord:
    id: int
    job_id: int
    url: str
    first_seen_at: datetime
    last_seen_at: datetime


def _record(job: Job) -> JobRecord:
    return JobRecord(
        id=job.id,
        company_slug=job.company_slug,
        url_key=job.url_key,
        status=job.status,
        next_phase=job.next_phase,
        attempt_count=job.attempt_count,
        next_attempt_after=job.next_attempt_after,
        current_attempt_id=job.current_attempt_id,
        current_content_id=job.current_content_id,
        reprocess_requested=job.reprocess_requested,
        merged_into_id=job.merged_into_id,
        first_seen_at=job.first_seen_at,
        last_seen_at=job.last_seen_at,
        updated_at=job.updated_at,
    )


async def observe(
    session: AsyncSession,
    company_slug: str,
    observations: Sequence[Observation | tuple[str, str]],
    seen_at: datetime,
) -> ObserveSummary:
    moment = as_naive_utc(seen_at)
    normalized = tuple(
        item if isinstance(item, Observation) else Observation(*item)
        for item in observations
    )
    keys = tuple(dict.fromkeys(item.url_key for item in normalized))

    async with session.begin():
        company_result = await session.execute(
            select(Job).where(Job.company_slug == company_slug)
        )
        company_jobs = {job.id: job for job in company_result.scalars()}
        existing = {job.url_key: job for job in company_jobs.values()}
        new_count = 0
        targets: dict[str, Job] = {}
        for key in keys:
            job = existing.get(key)
            if job is None:
                job = Job(
                    company_slug=company_slug,
                    url_key=key,
                    status="new",
                    next_phase="fetch",
                    attempt_count=0,
                    first_seen_at=moment,
                    last_seen_at=moment,
                    updated_at=moment,
                )
                session.add(job)
                await session.flush()
                company_jobs[job.id] = job
                existing[key] = job
                new_count += 1
            target = job
            seen: set[int] = set()
            while target.merged_into_id is not None:
                if target.id in seen:
                    raise RuntimeError(f"merge cycle detected at job {target.id}")
                seen.add(target.id)
                parent = company_jobs.get(target.merged_into_id)
                if parent is None:
                    raise RuntimeError(
                        f"merged job {target.id} has missing canonical job"
                    )
                target = parent
            target.last_seen_at = moment
            target.updated_at = moment
            targets[key] = target

        for item in normalized:
            job = targets[item.url_key]
            source_result = await session.execute(
                select(JobSource).where(
                    JobSource.job_id == job.id,
                    JobSource.url == item.raw_url,
                )
            )
            source = source_result.scalar_one_or_none()
            if source is None:
                session.add(
                    JobSource(
                        job_id=job.id,
                        url=item.raw_url,
                        first_seen_at=moment,
                        last_seen_at=moment,
                    )
                )
            else:
                source.last_seen_at = moment

    return ObserveSummary(
        new=new_count,
        reseen=len(keys) - new_count,
        snapshot_keys=frozenset(job.url_key for job in targets.values()),
    )


async def claim(session: AsyncSession, job_id: int, *, reprocess: bool = False) -> bool:
    moment = utc_now()
    eligible = (
        (Job.status == "retry")
        & Job.reprocess_requested.is_(True)
        & (Job.next_attempt_after <= moment)
        if reprocess
        else Job.status.in_(("new", "retry"))
        & Job.reprocess_requested.is_(False)
        & ((Job.status == "new") | (Job.next_attempt_after <= moment))
    )
    async with session.begin():
        result = await session.execute(
            update(Job)
            .where(Job.id == job_id, eligible)
            .values(
                status="in_progress",
                attempt_count=Job.attempt_count + 1,
                next_attempt_after=None,
                reprocess_requested=False,
                updated_at=moment,
            )
            .returning(Job.id)
        )
    return result.scalar_one_or_none() is not None


async def advance_phase(
    session: AsyncSession,
    job_id: int,
    next_phase: str,
    *,
    attempt_id: int | None = None,
) -> bool:
    ownership = (
        Job.current_attempt_id == attempt_id
        if attempt_id is not None
        else Job.current_attempt_id.is_(None)
    )
    async with session.begin():
        result = await session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "in_progress", ownership)
            .values(next_phase=next_phase, updated_at=utc_now())
            .returning(Job.id)
        )
    return result.scalar_one_or_none() is not None


async def release(
    session: AsyncSession,
    job_id: int,
    *,
    status: str,
    next_phase: str,
    next_attempt_after: datetime | None,
    attempt_id: int | None = None,
) -> bool:
    if (status == "retry") != (next_attempt_after is not None):
        raise ValueError("next_attempt_after must be set exactly for retry jobs")
    retry_at = (
        as_naive_utc(next_attempt_after) if next_attempt_after is not None else None
    )
    ownership = (
        Job.current_attempt_id == attempt_id
        if attempt_id is not None
        else Job.current_attempt_id.is_(None)
    )
    async with session.begin():
        result = await session.execute(
            update(Job)
            .where(Job.id == job_id, ownership)
            .values(
                status=status,
                next_phase=next_phase,
                next_attempt_after=retry_at,
                current_attempt_id=None,
                updated_at=utc_now(),
            )
            .returning(Job.id)
        )
    return result.scalar_one_or_none() is not None


async def _merge_into(
    session: AsyncSession,
    job_id: int,
    into_id: int,
    moment: datetime,
    *,
    allow_in_progress: bool,
) -> bool:
    """Fold *job_id* into *into_id*; return whether the job row was claimed.

    The job row is flipped first and the sources move only when that flip
    touched a row, so a job another worker owns right now is left alone
    rather than having its sources taken and its status changed under it.
    A caller merging the job it owns passes ``allow_in_progress=True`` after
    verifying its own lease.
    """
    conditions = [Job.id == job_id, Job.id > into_id]
    if not allow_in_progress:
        conditions.append(Job.status != "in_progress")
    flipped = await session.execute(
        update(Job)
        .where(*conditions)
        .values(
            status="merged",
            merged_into_id=into_id,
            next_attempt_after=None,
            current_attempt_id=None,
            updated_at=moment,
        )
        .returning(Job.id)
    )
    if flipped.scalar_one_or_none() is None:
        return False
    existing_sources = select(JobSource.url).where(JobSource.job_id == into_id)
    await session.execute(
        delete(JobSource).where(
            JobSource.job_id == job_id,
            JobSource.url.in_(existing_sources),
        )
    )
    await session.execute(
        update(JobSource).where(JobSource.job_id == job_id).values(job_id=into_id)
    )
    return True


async def mark_merged(session: AsyncSession, job_id: int, into_id: int) -> None:
    if into_id >= job_id:
        raise ValueError("duplicates must merge into the lower job ID")
    async with session.begin():
        pair = await session.execute(
            select(Job.id, Job.company_slug, Job.status).where(
                Job.id.in_((job_id, into_id))
            )
        )
        rows = {row.id: row for row in pair}
        if set(rows) != {job_id, into_id}:
            raise ValueError("both merge jobs must exist")
        if rows[job_id].company_slug != rows[into_id].company_slug:
            raise ValueError("duplicates must belong to the same company")
        if rows[into_id].status == "merged":
            raise ValueError("merge target must be canonical")
        merged = await _merge_into(
            session, job_id, into_id, utc_now(), allow_in_progress=False
        )
        if not merged:
            raise ValueError("merge source is being processed")


def _comparable_title(title: str | None) -> str | None:
    if title is None:
        return None
    folded = " ".join(title.casefold().split())
    return folded or None


async def _latest_title(session: AsyncSession, job_id: int) -> str | None:
    result = await session.execute(
        select(JobContent.title)
        .where(JobContent.job_id == job_id)
        .order_by(JobContent.id.desc())
        .limit(1)
    )
    return _comparable_title(result.scalar_one_or_none())


async def merge_duplicate(
    session: AsyncSession,
    *,
    job_id: int,
    company_slug: str,
    requisition_id: str | None,
    fingerprint: str,
    attempt_id: int | None = None,
) -> int | None:
    """Merge *job_id* with its one proven duplicate, if any; return the canonical.

    Conservative by design, because a merge cannot be undone through the
    CLI. A requisition id counts only when exactly one other live job carries
    it and the two titles agree, so a board that stamps every posting with
    the same identifier never collapses into one job. A fingerprint counts
    only when exactly one other live job shares it. The lower id is always
    the canonical, and a duplicate that another worker is processing right
    now is left for that worker.
    """
    async with session.begin():
        if attempt_id is not None:
            owner = await session.execute(
                select(Job.id).where(
                    Job.id == job_id,
                    Job.status == "in_progress",
                    Job.current_attempt_id == attempt_id,
                )
            )
            if owner.scalar_one_or_none() is None:
                return None

        async def others_matching(predicate: ColumnElement[bool]) -> tuple[int, ...]:
            result = await session.execute(
                select(Job.id)
                .join(JobContent, JobContent.job_id == Job.id)
                .where(
                    Job.id != job_id,
                    Job.company_slug == company_slug,
                    Job.status != "merged",
                    Job.merged_into_id.is_(None),
                    predicate,
                )
                .distinct()
                .order_by(Job.id)
            )
            return tuple(result.scalars())

        match: int | None = None
        if requisition_id is not None:
            by_requisition = await others_matching(
                JobContent.requisition_id == requisition_id
            )
            if len(by_requisition) == 1:
                own_title = await _latest_title(session, job_id)
                other_title = await _latest_title(session, by_requisition[0])
                if own_title is not None and own_title == other_title:
                    match = by_requisition[0]
        if match is None:
            by_fingerprint = await others_matching(
                JobContent.fingerprint == fingerprint
            )
            if len(by_fingerprint) == 1:
                match = by_fingerprint[0]
        if match is None:
            return job_id

        moment = utc_now()
        if match < job_id:
            merged = await _merge_into(
                session, job_id, match, moment, allow_in_progress=True
            )
            return match if merged else job_id
        await _merge_into(session, match, job_id, moment, allow_in_progress=False)
        return job_id


def _snapshot_chunks(
    snapshot_keys: Collection[tuple[str, str]], chunk_size: int = 500
) -> Iterable[tuple[str, tuple[str, ...]]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for company_slug, key in snapshot_keys:
        grouped[company_slug].append(key)
    for company_slug in sorted(grouped):
        keys = tuple(dict.fromkeys(grouped[company_slug]))
        for offset in range(0, len(keys), chunk_size):
            yield company_slug, keys[offset : offset + chunk_size]


async def list_work(
    session: AsyncSession,
    snapshot_keys: Collection[tuple[str, str]],
    now: datetime,
    limit: int | None,
) -> tuple[JobRecord, ...]:
    if not snapshot_keys or limit == 0:
        return ()
    moment = as_naive_utc(now)
    jobs: dict[int, JobRecord] = {}
    for company_slug, keys in _snapshot_chunks(snapshot_keys):
        statement: Select[tuple[Job]] = (
            select(Job)
            .where(
                Job.company_slug == company_slug,
                Job.url_key.in_(keys),
                or_(
                    Job.status == "new",
                    and_(
                        Job.status == "retry",
                        Job.reprocess_requested.is_(False),
                        Job.next_attempt_after <= moment,
                    ),
                ),
            )
            .order_by(Job.id)
        )
        result = await session.execute(statement)
        for job in result.scalars():
            jobs[job.id] = _record(job)
    ordered = tuple(jobs[job_id] for job_id in sorted(jobs))
    return ordered if limit is None else ordered[:limit]


async def get(session: AsyncSession, job_id: int) -> JobRecord | None:
    job = await session.get(Job, job_id)
    return _record(job) if job is not None else None


async def list_by_url(session: AsyncSession, raw_url: str) -> tuple[JobRecord, ...]:
    result = await session.execute(
        select(Job)
        .join(JobSource, JobSource.job_id == Job.id)
        .where(JobSource.url == raw_url)
        .order_by(Job.id)
    )
    return tuple(_record(job) for job in result.scalars())


async def list_sources(
    session: AsyncSession, job_id: int
) -> tuple[JobSourceRecord, ...]:
    result = await session.execute(
        select(JobSource).where(JobSource.job_id == job_id).order_by(JobSource.id)
    )
    return tuple(
        JobSourceRecord(
            id=source.id,
            job_id=source.job_id,
            url=source.url,
            first_seen_at=source.first_seen_at,
            last_seen_at=source.last_seen_at,
        )
        for source in result.scalars()
    )


async def list_by_ids(
    session: AsyncSession, job_ids: Collection[int]
) -> tuple[JobRecord, ...]:
    if not job_ids:
        return ()
    result = await session.execute(
        select(Job).where(Job.id.in_(job_ids)).order_by(Job.id)
    )
    return tuple(_record(job) for job in result.scalars())


async def list_by_companies(
    session: AsyncSession, company_slugs: Collection[str]
) -> tuple[JobRecord, ...]:
    if not company_slugs:
        return ()
    result = await session.execute(
        select(Job).where(Job.company_slug.in_(company_slugs)).order_by(Job.id)
    )
    return tuple(_record(job) for job in result.scalars())


async def list_by_status(
    session: AsyncSession, company_slug: str, status: str
) -> tuple[JobRecord, ...]:
    result = await session.execute(
        select(Job)
        .where(Job.company_slug == company_slug, Job.status == status)
        .order_by(Job.id)
    )
    return tuple(_record(job) for job in result.scalars())


async def prepare_reprocess(
    session: AsyncSession,
    job_ids: Collection[int],
    *,
    from_phase: str,
) -> tuple[int, ...]:
    ids = tuple(dict.fromkeys(job_ids))
    if not ids:
        return ()
    moment = utc_now()
    async with session.begin():
        selected_result = await session.execute(
            select(Job).where(Job.id.in_(ids)).order_by(Job.id)
        )
        selected = tuple(selected_result.scalars())
        found = {job.id for job in selected}
        missing = [job_id for job_id in ids if job_id not in found]
        if missing:
            raise ValueError(f"unknown job IDs: {', '.join(map(str, missing))}")
        active = [job.id for job in selected if job.status == "in_progress"]
        if active:
            raise ValueError(f"active job IDs: {', '.join(map(str, active))}")
        merged = [job.id for job in selected if job.status == "merged"]
        if merged:
            raise ValueError(f"merged job IDs: {', '.join(map(str, merged))}")
        content_ids: dict[int, int] = {}
        if from_phase == "eligibility":
            for job in selected:
                if job.current_content_id is None:
                    raise ValueError(f"job {job.id} has no content checkpoint")
                content_result = await session.execute(
                    select(JobContent.id).where(
                        JobContent.id == job.current_content_id,
                        JobContent.job_id == job.id,
                    )
                )
                content_id = content_result.scalar_one_or_none()
                if content_id is None:
                    raise ValueError(f"job {job.id} has no content checkpoint")
                content_ids[job.id] = content_id
        elif from_phase == "extraction":
            for job in selected:
                decision_result = await session.execute(
                    select(JobDecision).where(
                        JobDecision.job_id == job.id,
                        JobDecision.is_current.is_(True),
                        JobDecision.decision == "eligible",
                    )
                )
                decision = decision_result.scalar_one_or_none()
                if decision is None:
                    raise ValueError(
                        f"job {job.id} has no current eligible decision checkpoint"
                    )
                content_exists = await session.execute(
                    select(JobContent.id).where(
                        JobContent.id == decision.content_id,
                        JobContent.job_id == job.id,
                    )
                )
                if content_exists.scalar_one_or_none() is None:
                    raise ValueError(
                        f"job {job.id} has no content for its decision checkpoint"
                    )
                content_ids[job.id] = decision.content_id
        if from_phase in {"fetch", "eligibility"}:
            await session.execute(
                update(JobDecision)
                .where(
                    JobDecision.job_id.in_(ids),
                    JobDecision.is_current.is_(True),
                )
                .values(is_current=False)
            )
        await session.execute(
            update(JobResult)
            .where(JobResult.job_id.in_(ids), JobResult.is_current.is_(True))
            .values(is_current=False)
        )
        for job in selected:
            prepared = await session.execute(
                update(Job)
                .where(
                    Job.id == job.id,
                    Job.status != "in_progress",
                    Job.updated_at == job.updated_at,
                )
                .values(
                    status="retry",
                    next_phase=from_phase,
                    attempt_count=0,
                    next_attempt_after=moment,
                    current_attempt_id=None,
                    current_content_id=(
                        None if from_phase == "fetch" else content_ids[job.id]
                    ),
                    reprocess_requested=True,
                    merged_into_id=None,
                    updated_at=moment,
                )
                .returning(Job.id)
            )
            if prepared.scalar_one_or_none() is None:
                raise ValueError(f"job {job.id} changed while preparing reprocess")
    return tuple(sorted(ids))


async def _find_by_content(
    session: AsyncSession,
    company_slug: str,
    predicate: ColumnElement[bool],
    excluding_job_id: int | None,
) -> JobRecord | None:
    statement = (
        select(Job)
        .join(JobContent, JobContent.job_id == Job.id)
        .where(
            Job.company_slug == company_slug,
            Job.status != "merged",
            Job.merged_into_id.is_(None),
            predicate,
        )
        .order_by(Job.id)
        .limit(1)
    )
    if excluding_job_id is not None:
        statement = statement.where(Job.id != excluding_job_id)
    result = await session.execute(statement)
    job = result.scalar_one_or_none()
    return _record(job) if job is not None else None


async def find_by_requisition(
    session: AsyncSession,
    company_slug: str,
    requisition_id: str,
    *,
    excluding_job_id: int,
) -> JobRecord | None:
    return await _find_by_content(
        session,
        company_slug,
        JobContent.requisition_id == requisition_id,
        excluding_job_id,
    )


async def find_by_fingerprint(
    session: AsyncSession,
    company_slug: str,
    fingerprint: str,
    *,
    excluding_job_id: int,
) -> JobRecord | None:
    return await _find_by_content(
        session,
        company_slug,
        JobContent.fingerprint == fingerprint,
        excluding_job_id,
    )


async def disposition_counts(
    session: AsyncSession,
    snapshot_keys: Collection[tuple[str, str]],
    now: datetime | None = None,
) -> dict[str, int]:
    moment = as_naive_utc(now or utc_now())
    counts: Counter[str] = Counter()
    for company_slug, keys in _snapshot_chunks(snapshot_keys):
        result = await session.execute(
            select(Job.status, Job.next_attempt_after).where(
                Job.company_slug == company_slug,
                Job.url_key.in_(keys),
            )
        )
        for status, next_attempt_after in result:
            if status == "retry":
                disposition = (
                    "retry_due"
                    if next_attempt_after is not None and next_attempt_after <= moment
                    else "retry_waiting"
                )
                counts[disposition] += 1
            else:
                counts[status] += 1
    return dict(counts)
