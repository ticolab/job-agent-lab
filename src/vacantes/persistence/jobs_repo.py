"""The current job-URL set for a company: one atomic replace.

No history is kept, so a successful extraction replaces a company's URL
set wholesale. Delete-and-insert inside one transaction is simpler than
upsert-plus-prune and, unlike it, cannot leave a stale URL behind: there
is no "which rows did I not see this time" bookkeeping to get wrong.

The transaction is the failure boundary. A crash before it leaves the
previous snapshot intact, a crash inside it rolls back to that same
snapshot, and a concurrent reader never observes a partially replaced
set. That is the property that makes it safe to run this while an
operator is querying the database.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from vacantes.persistence.models import JobUrl
from vacantes.persistence.timestamps import as_naive_utc


async def replace_company_urls(
    session: AsyncSession,
    *,
    company_slug: str,
    urls: Sequence[str],
    captured_at: datetime,
) -> int:
    """Replace *company_slug*'s entire URL set with *urls*.

    Duplicates are collapsed with :func:`dict.fromkeys`, which keeps the
    strategy's ordering rather than the arbitrary order a ``set`` would
    produce. That satisfies the ``UNIQUE (company_slug, url)``
    constraint without an ``ON CONFLICT`` clause, and it means a board
    that lists the same posting in two sections is recorded once.

    An empty *urls* is a legitimate input, not a guard clause: a board
    whose postings all closed genuinely has zero open URLs, and the
    delete must still run so the previous set is cleared. Callers
    distinguish "no postings" from "extraction failed" through the run
    status, never through this function.

    Args:
        session: A session with no transaction in progress; this
            function owns its transaction.
        company_slug: The company whose set is being replaced. Must
            already exist in ``companies`` — the foreign key is
            enforced, since the engine sets ``PRAGMA foreign_keys=ON``.
        urls: The complete current set. Order is preserved.
        captured_at: When the extraction observed this set. Normalized
            to naive UTC.

    Returns:
        The number of distinct URLs written.
    """
    moment = as_naive_utc(captured_at)
    deduplicated = list(dict.fromkeys(urls))

    async with session.begin():
        await session.execute(delete(JobUrl).where(JobUrl.company_slug == company_slug))
        session.add_all(
            [
                JobUrl(company_slug=company_slug, url=url, captured_at=moment)
                for url in deduplicated
            ]
        )

    return len(deduplicated)


async def list_company_urls(
    session: AsyncSession, *, company_slug: str
) -> tuple[str, ...]:
    """Return *company_slug*'s current URL set in insertion order.

    The read side of :func:`replace_company_urls`, ordered by primary
    key so the result reproduces the order the strategy emitted. Exists
    so callers and tests can observe the stored set without building a
    query outside this package.
    """
    result = await session.execute(
        select(JobUrl.url)
        .where(JobUrl.company_slug == company_slug)
        .order_by(JobUrl.id)
    )
    return tuple(result.scalars().all())
