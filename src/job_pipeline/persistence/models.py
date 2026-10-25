"""SQLAlchemy models for the durable job-processing history.

Pipeline jobs restrict company deletion so historical work cannot disappear with
a catalog projection. All seven models share discovery's ``Base`` so company
foreign keys resolve and Alembic compares one metadata graph. Repositories
serialize replacement of current decisions and results because Alembic does not
portably render the SQLite partial indexes that could enforce that condition.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from job_discovery.persistence.models import Base

JOB_STATUSES = (
    "new",
    "in_progress",
    "retry",
    "completed_eligible",
    "completed_ineligible",
    "unknown",
    "unavailable",
    "failed",
    "merged",
)
PHASES = ("fetch", "eligibility", "extraction")
ATTEMPT_OUTCOMES = (
    "completed_eligible",
    "completed_ineligible",
    "unknown",
    "unavailable",
    "retry",
    "failed",
    "merged",
    "interrupted",
    "skipped",
)
DECISIONS = ("eligible", "ineligible", "unknown")
TRIGGERS = ("scheduled", "reprocess")
ERROR_CATEGORIES = (
    "not_found",
    "closed",
    "blocked",
    "timeout",
    "transport",
    "server_error",
    "empty_content",
    "off_site_redirect",
    "malformed",
    "model_call",
    "validation_failed",
    "ungrounded_evidence",
    "internal",
    "interrupted",
)


def _allowed(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(_allowed("status", JOB_STATUSES), name="ck_jobs_status"),
        CheckConstraint(_allowed("next_phase", PHASES), name="ck_jobs_next_phase"),
        CheckConstraint(
            "(status = 'retry') = (next_attempt_after IS NOT NULL)",
            name="ck_jobs_retry_due",
        ),
        CheckConstraint(
            "merged_into_id IS NULL OR merged_into_id != id",
            name="ck_jobs_not_self_merged",
        ),
        UniqueConstraint("company_slug", "url_key", name="uq_jobs_company_url_key"),
        Index("ix_jobs_status_next_attempt", "status", "next_attempt_after"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_slug: Mapped[str] = mapped_column(
        String,
        ForeignKey("companies.slug", ondelete="RESTRICT"),
        nullable=False,
    )
    url_key: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    next_phase: Mapped[str] = mapped_column(String, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_after: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    current_attempt_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_content_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reprocess_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    merged_into_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("jobs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class JobSource(Base):
    __tablename__ = "job_sources"
    __table_args__ = (UniqueConstraint("job_id", "url", name="uq_job_sources_job_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    url: Mapped[str] = mapped_column(String, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class JobContent(Base):
    __tablename__ = "job_contents"
    __table_args__ = (
        UniqueConstraint("job_id", "content_hash", name="uq_job_contents_job_hash"),
        Index("ix_job_contents_fingerprint", "fingerprint"),
        Index("ix_job_contents_requisition", "requisition_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    requisition_id: Mapped[str | None] = mapped_column(String, nullable=True)
    resolved_url: Mapped[str] = mapped_column(String, nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    method: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    jsonld_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    selection: Mapped[str] = mapped_column(Text, nullable=False)
    counts_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str] = mapped_column(String, nullable=False)


class JobAttempt(Base):
    __tablename__ = "job_attempts"
    __table_args__ = (
        CheckConstraint(_allowed("trigger", TRIGGERS), name="ck_job_attempts_trigger"),
        CheckConstraint(
            _allowed("phase_reached", PHASES),
            name="ck_job_attempts_phase_reached",
        ),
        CheckConstraint(
            _allowed("outcome", ATTEMPT_OUTCOMES),
            name="ck_job_attempts_outcome",
        ),
        CheckConstraint(
            _allowed("error_category", ERROR_CATEGORIES),
            name="ck_job_attempts_error_category",
        ),
        Index("ix_job_attempts_job_started", "job_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("pipeline_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    trigger: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    phase_reached: Mapped[str | None] = mapped_column(String, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String, nullable=True)
    error_category: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str] = mapped_column(String, nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    policy_version: Mapped[str] = mapped_column(String, nullable=False)
    extractor_version: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class JobDecision(Base):
    __tablename__ = "job_decisions"
    __table_args__ = (
        CheckConstraint(
            _allowed("decision", DECISIONS), name="ck_job_decisions_decision"
        ),
        Index("ix_job_decisions_job_current", "job_id", "is_current"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("job_attempts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    content_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("job_contents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    decision: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False)
    policy_version: Mapped[str] = mapped_column(String, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class JobResult(Base):
    __tablename__ = "job_results"
    __table_args__ = (Index("ix_job_results_job_current", "job_id", "is_current"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("job_attempts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    content_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("job_contents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    extractor_version: Mapped[str] = mapped_column(String, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
