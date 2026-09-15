"""Projects the catalog into the ``companies`` table.

``vacantes.catalog.companies`` is the source of truth for the corpus.
This module only ever copies it forward so the database is
self-describing: without it, ``job_urls`` is a set of slugs with no
names, board URLs, or human-counted targets, and every ad-hoc SQL query
would need the Python catalog open beside it.

The sync is refreshed at the start of every batch, which makes the
projection eventually consistent with the catalog by construction — an
edit to ``COMPANIES`` needs no migration and no manual reconciliation
step.

Note that this module takes the companies to sync as an argument rather
than importing ``COMPANIES`` itself. That is what keeps the persistence
package's imports down to ``domain`` and ``settings``, as
``tests/unit/test_layering.py`` enforces, and it is also what lets the
batch layer sync a subset when an operator selects specific handles.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from vacantes.domain.company import Company
from vacantes.persistence import models
from vacantes.persistence.timestamps import utc_now


async def sync_catalog(session: AsyncSession, companies: Sequence[Company]) -> None:
    """Insert or update the projection row for each of *companies*.

    Uses the ORM's ``merge`` — a select-then-insert-or-update per row —
    rather than a dialect-specific ``INSERT ... ON CONFLICT``. At 250
    rows a few times a week the cost is irrelevant, and staying on
    portable ORM operations keeps the documented PostgreSQL move a
    connection-string change rather than a rewrite of this function.

    Rows for companies **removed** from the catalog are deliberately
    left in place. Deleting one would cascade into its ``job_urls`` and
    ``company_runs``, discarding data as a side effect of an unrelated
    catalog edit; a genuine removal is an explicit operator decision,
    not something a routine sync should infer.

    Args:
        session: A session with no transaction in progress; this
            function owns its transaction.
        companies: The domain entities to project. Each row is keyed by
            :attr:`Company.slug`, so the projection cannot drift from
            the name it was derived from.
    """
    synced_at = utc_now()

    async with session.begin():
        for company in companies:
            await session.merge(
                models.Company(
                    slug=company.slug,
                    name=company.name,
                    job_board_url=company.job_board_url,
                    strategy=company.strategy,
                    expected_jobs=company.expected_jobs,
                    synced_at=synced_at,
                )
            )
