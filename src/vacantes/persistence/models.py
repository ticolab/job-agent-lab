"""SQLAlchemy 2.x declarative schema — three tables.

The schema is deliberately small, and two of its choices are
load-bearing enough to state here rather than leave to the reader.

**Why ``job_urls`` and ``company_runs`` are separate.** ``job_urls``
answers "what is open"; ``company_runs`` answers "did we successfully
look, and when". They have different lifetimes: the URL set is replaced
wholesale on every success, while the run history accumulates. Merging
them behind one ``updated_at`` would make a failed run indistinguishable
from a successful one to the freshness query, and a board that broke
this morning would stop being retried.

**Why ``companies`` exists at all**, given that
``vacantes.catalog.companies`` is the source of truth: without it the
database is a set of slugs with no names, URLs, or targets, and every
ad-hoc query needs the Python catalog beside it. It is a *projection*,
refreshed at the start of every batch, never an owner — which is why it
carries ``synced_at`` and no editorial columns.

Timestamps are naive UTC throughout; see
:mod:`vacantes.persistence.timestamps` for why that is an invariant
rather than a convention.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# The three values ``company_runs.status`` may take. Defined once here
# and reused by the CHECK constraint so the database rejects a typo that
# Python's type checker would otherwise be the only guard against.
RUN_STATUSES: tuple[str, ...] = ("in_progress", "success", "failed")


class Base(DeclarativeBase):
    """Declarative base for every model in this package.

    Alembic's ``env.py`` reads ``Base.metadata`` as its autogenerate
    target, so a model that is not reachable from this base is invisible
    to migrations.
    """


class Company(Base):
    """A projection of one ``catalog.COMPANIES`` entry.

    Named ``Company`` to match the domain entity it projects, and always
    imported qualified (``models.Company``) where both are in scope —
    the two are genuinely different things: the domain entity carries
    matcher rules, hooks, and ATS configs, while this row carries only
    what a SQL query needs to be self-describing.
    """

    __tablename__ = "companies"

    slug: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    job_board_url: Mapped[str] = mapped_column(String, nullable=False)
    strategy: Mapped[str] = mapped_column(String, nullable=False)
    # Nullable because it is a *human-counted* target: an entry that has
    # never been counted has no honest value, and 0 is a legitimate
    # count (a board with a location filter that has no options matching
    # the target region). Conflating "uncounted" with 0 would turn
    # verdicts from "unverified" into a false "match".
    expected_jobs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class CompanyRun(Base):
    """One attempt to extract one company's board.

    A row is created as ``in_progress`` when the worker starts and
    updated to ``success`` or ``failed`` exactly once. Companies skipped
    as fresh produce no row at all: this table records attempts, not
    considerations.
    """

    __tablename__ = "company_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('in_progress', 'success', 'failed')",
            name="ck_company_runs_status",
        ),
        # Covers the freshness lookup (slug + status + finished_at) and
        # the reaper's scan, which are the only two read patterns.
        Index(
            "ix_company_runs_lookup",
            "company_slug",
            "status",
            "finished_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_slug: Mapped[str] = mapped_column(
        String,
        ForeignKey("companies.slug", ondelete="CASCADE"),
        nullable=False,
    )
    batch_id: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # Null while in progress, and the reason the freshness query keys on
    # this column rather than started_at: an abandoned run has a start
    # but no finish.
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    url_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Stores what extraction.base.compute_verdict already produces
    # ("match", "under", "over", "unverified"), so drift from a
    # human-counted expected_jobs is a direct query rather than a
    # recomputation.
    verdict: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)


class JobUrl(Base):
    """One currently-open posting URL for one company.

    The whole set for a company is replaced on every successful run, so
    a row's presence means "open as of ``captured_at``" and its absence
    means "not open". No history is kept by design.
    """

    __tablename__ = "job_urls"
    __table_args__ = (
        # Makes the replace idempotent and catches a strategy that
        # emits the same posting twice. The repository de-duplicates
        # before insert, so this is a backstop, not the mechanism.
        UniqueConstraint("company_slug", "url", name="uq_job_urls_company_url"),
        Index("ix_job_urls_company", "company_slug"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_slug: Mapped[str] = mapped_column(
        String,
        ForeignKey("companies.slug", ondelete="CASCADE"),
        nullable=False,
    )
    url: Mapped[str] = mapped_column(String, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
