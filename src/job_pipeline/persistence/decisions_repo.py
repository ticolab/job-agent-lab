"""Current and historical eligibility decisions.

A one-shot decision stores the model's reason, supporting evidence (in the
legacy-named ``evidence_json`` column), conflicting evidence, the contract
version, and the prompt hash. Legacy rows keep a null prompt hash and null
conflicting evidence; they are auditable but never compatible for reuse or
extraction under the one-shot contract.
"""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from job_discovery.persistence.timestamps import as_naive_utc
from job_eligibility import (
    ELIGIBILITY_PROMPT_HASH,
    ELIGIBILITY_VERSION,
    MAX_EVIDENCE_ITEMS,
    EligibilityVerdict,
)
from job_pipeline.persistence import finalize
from job_pipeline.persistence.finalize import CheckpointError as CheckpointError
from job_pipeline.persistence.models import (
    DECISIONS,
    Job,
    JobAttempt,
    JobContent,
    JobDecision,
)

_TRANSITIONS = {
    "eligible": ("ready_for_extraction", "extraction"),
    "ineligible": ("completed_ineligible", "eligibility"),
    "unknown": ("unknown", "eligibility"),
}


@dataclass(frozen=True)
class DecisionInput:
    decision: str
    reason: str
    evidence_json: str
    policy_version: str
    conflicting_evidence_json: str | None = None
    prompt_hash: str | None = None


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    id: int
    job_id: int
    attempt_id: int
    content_id: int
    decision: str
    reason: str
    evidence_json: str
    policy_version: str
    is_current: bool
    decided_at: datetime
    conflicting_evidence_json: str | None = None
    prompt_hash: str | None = None

    @property
    def is_current_contract(self) -> bool:
        """Whether the row was decided under this binary's one-shot contract."""
        return (
            self.policy_version == ELIGIBILITY_VERSION
            and self.prompt_hash == ELIGIBILITY_PROMPT_HASH
        )


def _evidence_json(values: tuple[str, ...]) -> str:
    return json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))


def from_verdict(verdict: EligibilityVerdict) -> DecisionInput:
    """Serialize a grounded verdict into its persisted decision shape."""
    return DecisionInput(
        decision=verdict.decision,
        reason=verdict.reason,
        evidence_json=_evidence_json(verdict.supporting_evidence),
        conflicting_evidence_json=_evidence_json(verdict.conflicting_evidence),
        policy_version=verdict.eligibility_version,
        prompt_hash=verdict.prompt_hash,
    )


def evidence_list(raw: str, *, name: str = "evidence") -> list[str]:
    """Parse one stored evidence bucket, rejecting anything but nonblank strings."""
    try:
        values = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise CheckpointError(f"{name} must be valid JSON") from exc
    if not isinstance(values, list) or not all(
        isinstance(value, str) and value.strip() for value in values
    ):
        raise CheckpointError(f"{name} must be a list of nonblank strings")
    return values


def _validate_input_shape(decision: DecisionInput) -> None:
    if decision.decision not in DECISIONS:
        raise CheckpointError("decision must be eligible, ineligible, or unknown")
    if not decision.reason.strip():
        raise CheckpointError("decision reason must not be blank")
    if (decision.prompt_hash is None) != (decision.conflicting_evidence_json is None):
        raise CheckpointError(
            "prompt hash and conflicting evidence must be supplied together"
        )
    supporting = evidence_list(decision.evidence_json, name="supporting evidence")
    if decision.prompt_hash is None:
        if decision.policy_version == ELIGIBILITY_VERSION:
            raise CheckpointError("one-shot decisions require a prompt hash")
        return
    if (
        decision.policy_version != ELIGIBILITY_VERSION
        or decision.prompt_hash != ELIGIBILITY_PROMPT_HASH
    ):
        raise CheckpointError("decision uses an incompatible eligibility contract")
    assert decision.conflicting_evidence_json is not None
    conflicting = evidence_list(
        decision.conflicting_evidence_json, name="conflicting evidence"
    )
    if len(supporting) > MAX_EVIDENCE_ITEMS or len(conflicting) > MAX_EVIDENCE_ITEMS:
        raise CheckpointError("an evidence bucket exceeds its item limit")
    if not supporting and (conflicting or decision.decision != "unknown"):
        raise CheckpointError("only missing-information unknown may omit evidence")


def _record(decision: JobDecision) -> DecisionRecord:
    return DecisionRecord(
        id=decision.id,
        job_id=decision.job_id,
        attempt_id=decision.attempt_id,
        content_id=decision.content_id,
        decision=decision.decision,
        reason=decision.reason,
        evidence_json=decision.evidence_json,
        policy_version=decision.policy_version,
        is_current=decision.is_current,
        decided_at=decision.decided_at,
        conflicting_evidence_json=decision.conflicting_evidence_json,
        prompt_hash=decision.prompt_hash,
    )


async def _validate_ownership_of_rows(
    session: AsyncSession, *, job_id: int, attempt_id: int, content_id: int
) -> None:
    attempt = await session.execute(
        select(JobAttempt.id).where(
            JobAttempt.id == attempt_id, JobAttempt.job_id == job_id
        )
    )
    if attempt.scalar_one_or_none() is None:
        raise CheckpointError(f"attempt {attempt_id} does not belong to job {job_id}")
    content = await session.execute(
        select(JobContent.id).where(
            JobContent.id == content_id, JobContent.job_id == job_id
        )
    )
    if content.scalar_one_or_none() is None:
        raise CheckpointError(f"content {content_id} does not belong to job {job_id}")


async def _insert_current(
    session: AsyncSession,
    *,
    job_id: int,
    attempt_id: int,
    content_id: int,
    decision: DecisionInput,
    decided_at: datetime,
) -> int:
    await _validate_ownership_of_rows(
        session, job_id=job_id, attempt_id=attempt_id, content_id=content_id
    )
    await session.execute(
        update(JobDecision)
        .where(JobDecision.job_id == job_id, JobDecision.is_current.is_(True))
        .values(is_current=False)
    )
    row = JobDecision(
        job_id=job_id,
        attempt_id=attempt_id,
        content_id=content_id,
        decision=decision.decision,
        reason=decision.reason,
        evidence_json=decision.evidence_json,
        conflicting_evidence_json=decision.conflicting_evidence_json,
        policy_version=decision.policy_version,
        prompt_hash=decision.prompt_hash,
        is_current=True,
        decided_at=as_naive_utc(decided_at),
    )
    session.add(row)
    await session.flush()
    decision_id: int = row.id
    return decision_id


def _compatible_current(job_id: int, content_id: int) -> Select[tuple[JobDecision]]:
    return select(JobDecision).where(
        JobDecision.job_id == job_id,
        JobDecision.content_id == content_id,
        JobDecision.policy_version == ELIGIBILITY_VERSION,
        JobDecision.prompt_hash == ELIGIBILITY_PROMPT_HASH,
        JobDecision.is_current.is_(True),
    )


async def current_compatible(
    session: AsyncSession, *, job_id: int, content_id: int
) -> DecisionRecord | None:
    """The current decision on *content_id*, only if made under this contract."""
    result = await session.execute(_compatible_current(job_id, content_id))
    row = result.scalar_one_or_none()
    return _record(row) if row is not None else None


async def finalize_eligibility(
    session: AsyncSession,
    *,
    job_id: int,
    attempt_id: int,
    content_id: int,
    decision: DecisionInput | None,
    decided_at: datetime,
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> tuple[int, str] | None:
    """Persist or reuse one decision and end the attempt in one transaction.

    *decision* is a new grounded verdict, or ``None`` to reuse the current
    decision already recorded for this content under the same contract version
    and prompt hash. The job transition, lease release, decision row, and
    attempt finish commit together. Returns ``(decision_id, decision)``, or
    ``None`` when the attempt no longer owns the job.
    """
    if decision is not None:
        _validate_input_shape(decision)
        if decision.prompt_hash is None:
            raise CheckpointError(
                "eligibility finalization requires a one-shot verdict"
            )
    moment = as_naive_utc(decided_at)
    async with session.begin():
        # A lease-guarded no-op write takes SQLite's write lock first, so no
        # reaper or reclaim can steal the lease between this ownership check
        # and the release below: a stale owner is skipped, never failed.
        owned = await session.execute(
            update(Job)
            .where(
                Job.id == job_id,
                Job.status == "in_progress",
                Job.current_attempt_id == attempt_id,
                Job.current_content_id == content_id,
            )
            .values(updated_at=Job.updated_at)
            .returning(Job.id)
        )
        if owned.scalar_one_or_none() is None:
            return None
        identity = await session.execute(
            select(JobAttempt.id).where(
                JobAttempt.id == attempt_id,
                JobAttempt.job_id == job_id,
                JobAttempt.finished_at.is_(None),
                JobAttempt.policy_version == ELIGIBILITY_VERSION,
                JobAttempt.eligibility_prompt_hash == ELIGIBILITY_PROMPT_HASH,
            )
        )
        if identity.scalar_one_or_none() is None:
            raise CheckpointError(
                "eligibility attempt is finished or uses another contract identity"
            )
        if decision is None:
            reused = await session.execute(_compatible_current(job_id, content_id))
            row = reused.scalar_one_or_none()
            if row is None:
                raise CheckpointError(
                    f"job {job_id} has no current compatible decision "
                    f"on content {content_id}"
                )
            decision_id: int = row.id
            outcome = row.decision
        else:
            decision_id = await _insert_current(
                session,
                job_id=job_id,
                attempt_id=attempt_id,
                content_id=content_id,
                decision=decision,
                decided_at=moment,
            )
            outcome = decision.decision
        status, next_phase = _TRANSITIONS[outcome]
        released = await session.execute(
            update(Job)
            .where(
                Job.id == job_id,
                Job.status == "in_progress",
                Job.current_attempt_id == attempt_id,
                Job.current_content_id == content_id,
            )
            .values(
                status=status,
                next_phase=next_phase,
                next_attempt_after=None,
                current_attempt_id=None,
                attempt_count=0 if outcome == "eligible" else Job.attempt_count,
                updated_at=moment,
            )
            .returning(Job.id)
        )
        if released.scalar_one_or_none() is None:
            raise CheckpointError(f"job {job_id} lost its lease during finalization")
        await finalize.finish_owned_attempt(
            session,
            job_id=job_id,
            attempt_id=attempt_id,
            outcome=status,
            phase_reached="eligibility",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            finished_at=moment,
        )
    return decision_id, outcome


async def count_stale_ready(
    session: AsyncSession, company_slugs: Collection[str]
) -> int:
    """Ready jobs whose current decision was not made under this contract.

    Extraction refuses these with ``missing_checkpoint``; an operator clears
    them with ``reprocess --from-phase eligibility`` before activation.
    """
    if not company_slugs:
        return 0
    result = await session.execute(
        select(func.count(Job.id))
        .outerjoin(
            JobDecision,
            (JobDecision.job_id == Job.id) & JobDecision.is_current.is_(True),
        )
        .where(
            Job.company_slug.in_(tuple(company_slugs)),
            Job.status == "ready_for_extraction",
            or_(
                JobDecision.id.is_(None),
                JobDecision.policy_version != ELIGIBILITY_VERSION,
                JobDecision.prompt_hash.is_(None),
                JobDecision.prompt_hash != ELIGIBILITY_PROMPT_HASH,
            ),
        )
    )
    return int(result.scalar_one())


async def accept(
    session: AsyncSession,
    *,
    job_id: int,
    attempt_id: int,
    content_id: int,
    decision: DecisionInput,
    decided_at: datetime,
    require_ownership: bool = False,
) -> int | None:
    """Record one decision as current without changing job state."""
    _validate_input_shape(decision)
    async with session.begin():
        if require_ownership:
            owned = await session.execute(
                select(Job.id).where(
                    Job.id == job_id,
                    Job.status == "in_progress",
                    Job.current_attempt_id == attempt_id,
                    Job.current_content_id == content_id,
                )
            )
            if owned.scalar_one_or_none() is None:
                return None
        decision_id = await _insert_current(
            session,
            job_id=job_id,
            attempt_id=attempt_id,
            content_id=content_id,
            decision=decision,
            decided_at=decided_at,
        )
    return decision_id


async def current(session: AsyncSession, job_id: int) -> DecisionRecord | None:
    result = await session.execute(
        select(JobDecision).where(
            JobDecision.job_id == job_id,
            JobDecision.is_current.is_(True),
        )
    )
    decision = result.scalar_one_or_none()
    return _record(decision) if decision is not None else None


async def invalidate_current(session: AsyncSession, job_id: int) -> None:
    async with session.begin():
        await session.execute(
            update(JobDecision)
            .where(JobDecision.job_id == job_id, JobDecision.is_current.is_(True))
            .values(is_current=False)
        )


async def history(session: AsyncSession, job_id: int) -> tuple[DecisionRecord, ...]:
    result = await session.execute(
        select(JobDecision)
        .where(JobDecision.job_id == job_id)
        .order_by(JobDecision.decided_at, JobDecision.id)
    )
    return tuple(_record(decision) for decision in result.scalars())
