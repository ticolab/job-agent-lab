"""Batch entry point: reap, sync, fan out, aggregate.

:func:`run_batch` is the whole scheduler. This is deliberately "a
semaphore and a gather" rather than a workflow
framework: one operator, one machine, independent units of work, and no
task dependency graph to express. A framework earns its weight with
cross-run durable state or distributed workers, neither of which exists
here.

Startup does two things before any company runs, in this order:

1. **Reap abandoned runs.** A batch killed mid-flight leaves
   ``in_progress`` rows that would otherwise sit there forever, invisible
   to the freshness rule and never retried.
2. **Sync the catalog projection.** ``company_runs`` and ``job_urls``
   both carry a foreign key to ``companies``, so a company that has never
   been seen before must have its row before its run starts.

The scheduler receives its companies and its session factory as
arguments rather than importing ``COMPANIES`` or a global engine. That is
what keeps this package's layering row to three entries and lets the
whole thing run against a temporary database in a test.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import uuid4

from vacantes.batch.policy import BatchPolicy, ConcurrencyClass, concurrency_class
from vacantes.batch.worker import CompanyOutcome, SessionFactory, run_company
from vacantes.domain.company import Company
from vacantes.extraction.base import RunContext
from vacantes.persistence import catalog_repo, runs_repo
from vacantes.persistence.timestamps import utc_now


def new_batch_id() -> str:
    """Mint an identifier for one batch.

    Sortable-timestamp-first so ``order by batch_id`` is chronological
    without parsing, with four random hex characters appended because two
    batches started in the same second would otherwise share an id and
    silently merge in a ``group by``.
    """
    return f"{utc_now():%Y%m%d_%H%M%S}_{uuid4().hex[:4]}"


@dataclass(frozen=True)
class BatchResult:
    """What one batch did, ready to render or assert against.

    Attributes:
        batch_id: The id stamped on every ``company_runs`` row this
            batch wrote.
        outcomes: One :class:`CompanyOutcome` per company, in the order
            the companies were supplied — ``asyncio.gather`` preserves
            input order regardless of completion order.
        reaped: How many abandoned ``in_progress`` rows were failed at
            startup. Zero on a healthy run; non-zero means the previous
            batch did not exit cleanly.
    """

    batch_id: str
    outcomes: tuple[CompanyOutcome, ...]
    reaped: int

    def _count(self, status: str) -> int:
        return sum(1 for outcome in self.outcomes if outcome.status == status)

    @property
    def succeeded(self) -> int:
        """How many companies extracted successfully."""
        return self._count("success")

    @property
    def failed(self) -> int:
        """How many companies raised and were recorded as failed."""
        return self._count("failed")

    @property
    def skipped(self) -> int:
        """How many companies were fresh enough to leave alone."""
        return self._count("skipped")


async def run_batch(
    companies: Sequence[Company],
    ctx: RunContext,
    *,
    session_factory: SessionFactory,
    policy: BatchPolicy | None = None,
    batch_id: str | None = None,
) -> BatchResult:
    """Run every company in *companies*, bounded by *policy*'s ceilings.

    One task per company, each selecting its own semaphore from the class
    :func:`concurrency_class` assigns it. ``return_exceptions=True`` is
    deliberately not passed: the worker catches its own failures, so
    every task already resolves to a :class:`CompanyOutcome` and the
    caller never has to tell an outcome from an exception.

    Args:
        companies: The companies to run. Also the exact set projected
            into the ``companies`` table, so callers pass the slice they
            intend to run rather than the whole corpus.
        ctx: The shared :class:`RunContext`, passed through to every
            strategy untouched.
        session_factory: Opens sessions for the startup work and is
            handed to each worker.
        policy: Ceilings, freshness window, and timeout. Defaults to
            :class:`BatchPolicy`'s own defaults.
        batch_id: Override the generated id. Intended for tests and for
            an operator re-running a known batch.

    Returns:
        The aggregated :class:`BatchResult`.
    """
    policy = policy or BatchPolicy()
    batch_id = batch_id or new_batch_id()

    # One session per repository call: each opens its own transaction,
    # so sharing one would couple them through whatever state the
    # previous call happened to leave behind.
    async with session_factory() as session:
        reaped = await runs_repo.reap_stale_runs(
            session, older_than=utc_now() - policy.timeout
        )

    # Ordered after reaping only for tidiness, but strictly *before* any
    # worker starts: `company_runs.company_slug` and `job_urls`
    # .company_slug both reference `companies.slug`, so a company being
    # seen for the first time needs its projection row to exist.
    async with session_factory() as session:
        await catalog_repo.sync_catalog(session, companies)

    semaphores: dict[ConcurrencyClass, asyncio.Semaphore] = {
        "browser": asyncio.Semaphore(policy.browser_concurrency),
        "http": asyncio.Semaphore(policy.http_concurrency),
    }

    async def _guarded(company: Company) -> CompanyOutcome:
        async with semaphores[concurrency_class(company)]:
            return await run_company(
                company,
                ctx,
                session_factory=session_factory,
                policy=policy,
                batch_id=batch_id,
            )

    outcomes = await asyncio.gather(*(_guarded(company) for company in companies))
    return BatchResult(batch_id=batch_id, outcomes=tuple(outcomes), reaped=reaped)
