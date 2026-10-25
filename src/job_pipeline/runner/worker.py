"""One durable, failure-isolated pipeline job lifecycle."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, cast
from urllib.parse import urlsplit

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from job_discovery.domain.company import Company
from job_discovery.persistence.timestamps import utc_now
from job_discovery.settings import CaptureBrowser
from job_eligibility import (
    POLICY_VERSION,
    EligibilityResponse,
)
from job_eligibility import (
    build_messages as build_eligibility_messages,
)
from job_eligibility import (
    validate as validate_eligibility,
)
from job_extractor import (
    EXTRACTOR_VERSION,
    ExtractionResponse,
)
from job_extractor import (
    build_messages as build_extraction_messages,
)
from job_extractor import (
    validate as validate_extraction,
)
from job_pipeline.content.ladder import acquire
from job_pipeline.content.model import AcquiredContent, AcquisitionFailure
from job_pipeline.identity import fingerprint
from job_pipeline.llm import StructuredCaller
from job_pipeline.persistence import (
    attempts_repo,
    contents_repo,
    decisions_repo,
    jobs_repo,
    results_repo,
)
from job_pipeline.policy import PipelinePolicy, classify_failure, next_state

SessionFactory = async_sessionmaker[AsyncSession]
JobStatus = Literal[
    "skipped",
    "completed_eligible",
    "completed_ineligible",
    "unknown",
    "unavailable",
    "retry",
    "failed",
    "merged",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TokenUsage:
    prompt: int = 0
    completion: int = 0

    @property
    def total(self) -> int:
        return self.prompt + self.completion


@dataclass(frozen=True, slots=True)
class JobError:
    category: str
    message: str


@dataclass(frozen=True, slots=True)
class JobOutcome:
    job_id: int
    company_slug: str
    url_key: str
    status: JobStatus

    phase_reached: str
    decision: str | None
    error: JobError | None
    tokens: TokenUsage


@dataclass(frozen=True, slots=True)
class WorkerDeps:
    session_factory: SessionFactory
    http_client: httpx.AsyncClient
    browser: CaptureBrowser | None
    caller: StructuredCaller
    policy: PipelinePolicy
    run_id: str
    trigger: Literal["scheduled", "reprocess"]
    reason: str | None
    companies: Mapping[str, Company]
    http_semaphore: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(1)
    )
    browser_semaphore: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(1)
    )
    model_semaphore: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(1)
    )
    per_host_semaphores: dict[str, asyncio.Semaphore] = field(default_factory=dict)
    per_host_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass(slots=True)
class _Progress:
    phase: str
    decision: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def add_tokens(self, prompt: int | None, completion: int | None) -> None:
        self.prompt_tokens += prompt or 0
        self.completion_tokens += completion or 0

    @property
    def tokens(self) -> TokenUsage:
        return TokenUsage(self.prompt_tokens, self.completion_tokens)


async def _job(deps: WorkerDeps, job_id: int) -> jobs_repo.JobRecord | None:
    async with deps.session_factory() as session:
        return await jobs_repo.get(session, job_id)


async def _host_semaphore(deps: WorkerDeps, host: str) -> asyncio.Semaphore:
    async with deps.per_host_lock:
        semaphore = deps.per_host_semaphores.get(host)
        if semaphore is None:
            semaphore = asyncio.Semaphore(deps.policy.per_host_concurrency)
            deps.per_host_semaphores[host] = semaphore
        return semaphore


async def _timed[T](deps: WorkerDeps, step: Awaitable[T]) -> T:
    """Bound one network or model step by the policy's job timeout.

    Called only once the step holds its concurrency slot. Wrapping the whole
    lifecycle instead would charge queue time against the attempt: with
    every job of a corpus-sized run claimed at once and a handful of slots,
    most jobs would time out while waiting, burn an attempt, and repeat the
    next day until terminal.
    """
    return await asyncio.wait_for(step, timeout=deps.policy.job_timeout)


async def _acquire_content(
    deps: WorkerDeps,
    company: Company,
    url: str,
    *,
    force_rendered: bool,
) -> AcquiredContent | AcquisitionFailure:
    host = urlsplit(url).hostname or ""
    host_semaphore = await _host_semaphore(deps, host)
    if force_rendered and deps.browser is not None:
        async with deps.http_semaphore, host_semaphore, deps.browser_semaphore:
            return await _timed(
                deps,
                acquire(
                    url,
                    company=company,
                    http=deps.http_client,
                    browser=deps.browser,
                    force_rendered=True,
                ),
            )
    async with deps.http_semaphore, host_semaphore:
        acquired = await _timed(
            deps,
            acquire(
                url,
                company=company,
                http=deps.http_client,
                browser=None,
                force_rendered=False,
            ),
        )
    if (
        isinstance(acquired, AcquisitionFailure)
        and acquired.category == "empty_content"
        and deps.browser is not None
    ):
        async with deps.http_semaphore, host_semaphore, deps.browser_semaphore:
            return await _timed(
                deps,
                acquire(
                    url,
                    company=company,
                    http=deps.http_client,
                    browser=deps.browser,
                    force_rendered=True,
                ),
            )
    return acquired


async def _outcome(
    deps: WorkerDeps,
    job_id: int,
    company_slug: str,
    url_key: str,
    attempt_id: int,
    progress: _Progress,
    status: JobStatus,
    *,
    error: JobError | None = None,
    require_ownership: bool = False,
) -> JobOutcome:
    async with deps.session_factory() as session:
        await attempts_repo.finish(
            session,
            attempt_id,
            phase_reached=progress.phase,
            outcome=status,
            error_category=error.category if error is not None else None,
            error_message=error.message if error is not None else None,
            prompt_tokens=progress.prompt_tokens or None,
            completion_tokens=progress.completion_tokens or None,
            finished_at=utc_now(),
            require_ownership=require_ownership,
        )
    return JobOutcome(
        job_id=job_id,
        company_slug=company_slug,
        url_key=url_key,
        status=status,
        phase_reached=progress.phase,
        decision=progress.decision,
        error=error,
        tokens=progress.tokens,
    )


async def _release(
    deps: WorkerDeps,
    job_id: int,
    attempt_id: int,
    *,
    status: str,
    phase: str,
    next_attempt_after: datetime | None = None,
) -> bool:
    async with deps.session_factory() as session:
        return await jobs_repo.release(
            session,
            job_id,
            status=status,
            next_phase=phase,
            next_attempt_after=next_attempt_after,
            attempt_id=attempt_id,
        )


async def _fail(
    deps: WorkerDeps,
    job: jobs_repo.JobRecord,
    attempt_id: int,
    progress: _Progress,
    failure: BaseException | AcquisitionFailure,
) -> JobOutcome:
    category, retryable = classify_failure(failure)
    current = await _job(deps, job.id)
    if current is None:
        current = job
    raw_status, retry_at = next_state(current, category, retryable, deps.policy)
    status = cast(JobStatus, raw_status)
    message = (
        failure.message if isinstance(failure, AcquisitionFailure) else str(failure)
    )
    error = JobError(category, message or repr(failure))
    released = await _release(
        deps,
        job.id,
        attempt_id,
        status=status,
        phase=progress.phase,
        next_attempt_after=retry_at,
    )
    if not released:
        status = "skipped"
    return await _outcome(
        deps,
        job.id,
        job.company_slug,
        job.url_key,
        attempt_id,
        progress,
        status,
        error=error,
        require_ownership=False,
    )


async def _run_phases(
    job: jobs_repo.JobRecord,
    company: Company,
    attempt_id: int,
    deps: WorkerDeps,
    progress: _Progress,
) -> JobOutcome:
    content: contents_repo.ContentRecord | None = None
    content_id: int | None
    if job.next_phase == "fetch":
        progress.phase = "fetch"
        async with deps.session_factory() as session:
            sources = await jobs_repo.list_sources(session, job.id)
            last_error = await attempts_repo.last_error_category(session, job.id)
        if not sources:
            raise RuntimeError("job has no source URL")
        # The variant discovery saw most recently is the one most likely to
        # still resolve; an older alias may have left the board.
        source_url = max(
            sources, key=lambda source: (source.last_seen_at, source.id)
        ).url
        acquired = await _acquire_content(
            deps,
            company,
            source_url,
            force_rendered=last_error == "empty_content",
        )
        if isinstance(acquired, AcquisitionFailure):
            return await _fail(deps, job, attempt_id, progress, acquired)
        acquired_fingerprint = fingerprint(acquired.title or "", acquired.text)
        stored = contents_repo.AcquiredContent(
            content_hash=acquired.content_hash,
            fingerprint=acquired_fingerprint,
            requisition_id=acquired.requisition_id,
            resolved_url=acquired.resolved_url,
            http_status=acquired.http_status,
            method=acquired.method,
            title=acquired.title,
            text=acquired.text,
            jsonld_json=(
                json.dumps(acquired.jsonld, sort_keys=True)
                if acquired.jsonld is not None
                else None
            ),
            retrieved_at=acquired.retrieved_at,
        )
        async with deps.session_factory() as session:
            content_id = await contents_repo.store_checkpoint(
                session, job.id, attempt_id, stored
            )
        if content_id is None:
            return await _outcome(
                deps,
                job.id,
                job.company_slug,
                job.url_key,
                attempt_id,
                progress,
                "skipped",
            )
        async with deps.session_factory() as session:
            canonical_id = await jobs_repo.merge_duplicate(
                session,
                job_id=job.id,
                attempt_id=attempt_id,
                company_slug=job.company_slug,
                requisition_id=stored.requisition_id,
                fingerprint=stored.fingerprint,
            )
        if canonical_id is None:
            return await _outcome(
                deps,
                job.id,
                job.company_slug,
                job.url_key,
                attempt_id,
                progress,
                "skipped",
            )
        if canonical_id != job.id:
            return await _outcome(
                deps,
                job.id,
                job.company_slug,
                job.url_key,
                attempt_id,
                progress,
                "merged",
            )
        async with deps.session_factory() as session:
            advanced = await jobs_repo.advance_phase(
                session, job.id, "eligibility", attempt_id=attempt_id
            )
        if not advanced:
            return await _outcome(
                deps,
                job.id,
                job.company_slug,
                job.url_key,
                attempt_id,
                progress,
                "skipped",
            )
        content = contents_repo.ContentRecord(
            id=content_id,
            job_id=job.id,
            content_hash=stored.content_hash,
            fingerprint=stored.fingerprint,
            requisition_id=stored.requisition_id,
            resolved_url=stored.resolved_url,
            http_status=stored.http_status,
            method=stored.method,
            title=stored.title,
            text=stored.text,
            jsonld_json=stored.jsonld_json,
            retrieved_at=stored.retrieved_at,
        )

    if job.next_phase in {"fetch", "eligibility"}:
        progress.phase = "eligibility"
        if content is None:
            async with deps.session_factory() as session:
                content = await contents_repo.current(session, job.id)
        if content is None:
            raise RuntimeError("eligibility phase has no stored content checkpoint")
        async with deps.session_factory() as session:
            checkpoint_decision = await decisions_repo.current(session, job.id)
        if (
            checkpoint_decision is not None
            and checkpoint_decision.content_id == content.id
        ):
            progress.decision = checkpoint_decision.decision
            if checkpoint_decision.decision == "eligible":
                async with deps.session_factory() as session:
                    advanced = await jobs_repo.advance_phase(
                        session, job.id, "extraction", attempt_id=attempt_id
                    )
                if not advanced:
                    return await _outcome(
                        deps,
                        job.id,
                        job.company_slug,
                        job.url_key,
                        attempt_id,
                        progress,
                        "skipped",
                    )
            else:
                checkpoint_status: JobStatus = (
                    "completed_ineligible"
                    if checkpoint_decision.decision == "ineligible"
                    else "unknown"
                )
                released = await _release(
                    deps,
                    job.id,
                    attempt_id,
                    status=checkpoint_status,
                    phase="eligibility",
                )
                checkpoint_outcome: JobStatus = (
                    checkpoint_status if released else "skipped"
                )
                return await _outcome(
                    deps,
                    job.id,
                    job.company_slug,
                    job.url_key,
                    attempt_id,
                    progress,
                    checkpoint_outcome,
                )
        if progress.decision is None:
            async with deps.model_semaphore:
                eligibility_completion = await _timed(
                    deps,
                    deps.caller.complete(
                        build_eligibility_messages(content.text),
                        EligibilityResponse,
                    ),
                )
            progress.add_tokens(
                eligibility_completion.prompt_tokens,
                eligibility_completion.completion_tokens,
            )
            decision = validate_eligibility(eligibility_completion.parsed, content.text)
            progress.decision = decision.decision
            decision_input = decisions_repo.DecisionInput(
                decision=decision.decision,
                reason=decision.reason,
                evidence_json=json.dumps(decision.evidence),
                policy_version=decision.policy_version,
            )
            decision_status: JobStatus = (
                "completed_ineligible"
                if decision.decision == "ineligible"
                else "unknown"
            )
            async with deps.session_factory() as session:
                if decision.decision == "eligible":
                    checkpoint_id = await decisions_repo.accept_and_advance(
                        session,
                        job_id=job.id,
                        attempt_id=attempt_id,
                        content_id=content.id,
                        decision=decision_input,
                        decided_at=utc_now(),
                    )
                else:
                    checkpoint_id = await decisions_repo.accept_and_complete(
                        session,
                        job_id=job.id,
                        attempt_id=attempt_id,
                        content_id=content.id,
                        decision=decision_input,
                        status=decision_status,
                        decided_at=utc_now(),
                    )
            if checkpoint_id is None:
                return await _outcome(
                    deps,
                    job.id,
                    job.company_slug,
                    job.url_key,
                    attempt_id,
                    progress,
                    "skipped",
                )
            if decision.decision != "eligible":
                return await _outcome(
                    deps,
                    job.id,
                    job.company_slug,
                    job.url_key,
                    attempt_id,
                    progress,
                    decision_status,
                )

    progress.phase = "extraction"
    if content is None:
        async with deps.session_factory() as session:
            content = await contents_repo.current(session, job.id)
    if content is None:
        raise RuntimeError("extraction phase has no stored content checkpoint")
    if progress.decision is None:
        async with deps.session_factory() as session:
            current_decision = await decisions_repo.current(session, job.id)
        if current_decision is None or current_decision.decision != "eligible":
            raise RuntimeError("extraction phase has no eligible decision")
        progress.decision = current_decision.decision
    async with deps.model_semaphore:
        extraction_completion = await _timed(
            deps,
            deps.caller.complete(
                build_extraction_messages(content.text), ExtractionResponse
            ),
        )
    progress.add_tokens(
        extraction_completion.prompt_tokens,
        extraction_completion.completion_tokens,
    )
    result = validate_extraction(extraction_completion.parsed, content.text)
    async with deps.session_factory() as session:
        result_id = await results_repo.accept_and_complete(
            session,
            job_id=job.id,
            attempt_id=attempt_id,
            content_id=content.id,
            result=results_repo.ResultInput(
                extractor_version=result.extractor_version,
                schema_version=result.schema_version,
                result_json=result.model_dump_json(),
            ),
            accepted_at=utc_now(),
        )
    result_status: JobStatus = (
        "completed_eligible" if result_id is not None else "skipped"
    )
    return await _outcome(
        deps,
        job.id,
        job.company_slug,
        job.url_key,
        attempt_id,
        progress,
        result_status,
    )


async def process_job(job_id: int, *, deps: WorkerDeps) -> JobOutcome:
    job = await _job(deps, job_id)
    company_slug = job.company_slug if job is not None else "unknown"
    key = job.url_key if job is not None else "unknown"
    phase = job.next_phase if job is not None else "fetch"
    if job is None:
        return JobOutcome(
            job_id, company_slug, key, "skipped", phase, None, None, TokenUsage()
        )
    async with deps.session_factory() as session:
        attempt_id = await attempts_repo.claim_and_start(
            session,
            job_id=job_id,
            run_id=deps.run_id,
            trigger=deps.trigger,
            reason=deps.reason,
            model=deps.policy.model,
            policy_version=POLICY_VERSION,
            extractor_version=EXTRACTOR_VERSION,
            started_at=utc_now(),
        )
    if attempt_id is None:
        return JobOutcome(
            job_id, company_slug, key, "skipped", phase, None, None, TokenUsage()
        )
    claimed_job = await _job(deps, job_id)
    if claimed_job is None:
        return JobOutcome(
            job_id, company_slug, key, "skipped", phase, None, None, TokenUsage()
        )
    company = deps.companies.get(claimed_job.company_slug)
    if company is None:
        progress = _Progress(phase)
        return await _fail(
            deps,
            claimed_job,
            attempt_id,
            progress,
            RuntimeError(f"unknown company: {company_slug}"),
        )
    progress = _Progress(claimed_job.next_phase)
    try:
        # No ceiling on the lifecycle as a whole: each network and model
        # step is bounded by ``_timed`` once it holds its slot, and the
        # database calls between them are bounded by ``busy_timeout``.
        outcome = await _run_phases(claimed_job, company, attempt_id, deps, progress)
    except Exception as exc:
        try:
            outcome = await _fail(deps, claimed_job, attempt_id, progress, exc)
        except Exception:
            logger.warning(
                "could not record failure for job %s; the reaper will recover it",
                job_id,
                exc_info=True,
            )
            outcome = JobOutcome(
                job_id,
                company_slug,
                key,
                "failed",
                progress.phase,
                progress.decision,
                JobError("internal", repr(exc)),
                progress.tokens,
            )
    logger.info(
        "job slug=%s key=%s phase=%s outcome=%s",
        claimed_job.company_slug,
        claimed_job.url_key,
        outcome.phase_reached,
        outcome.status,
    )
    return outcome


__all__ = [
    "JobError",
    "JobOutcome",
    "JobStatus",
    "SessionFactory",
    "TokenUsage",
    "WorkerDeps",
    "process_job",
]
