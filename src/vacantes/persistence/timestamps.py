"""UTC timestamp helpers — the single normalization point for time.

SQLite has no native timestamp type: SQLAlchemy's SQLite dialect stores
a ``DateTime`` as an ISO-8601 *string*, and comparisons are therefore
lexicographic. That makes mixing timezone-aware and naive datetimes a
correctness bug rather than a style inconsistency, because an aware
value serializes with a ``+00:00`` offset suffix that a naive value
lacks, and the two no longer order correctly against each other.

The freshness rule is the exact place this would bite. Its predicate is
``finished_at >= cutoff``; if a run was written with an aware datetime
and the cutoff is computed naive, the comparison silently misjudges and
a company is either skipped when it should run or re-run when it should
be skipped. Nothing would fail loudly.

So this module defines the invariant: **every timestamp crossing the
persistence boundary is naive UTC.** Repositories normalize their
timestamp arguments through :func:`as_naive_utc` on the way in, which
makes the guarantee structural — a caller holding an aware datetime
cannot corrupt the ordering, and a caller holding a naive one is
assumed to already be UTC.

UTC rather than local time because a batch that straddles a daylight
saving transition would otherwise produce timestamps that go backwards.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return the current time as a naive UTC datetime.

    The naive-UTC form this layer stores. Prefer this over
    ``datetime.now()`` (which is local time) and over
    ``datetime.now(UTC)`` (which is aware) anywhere a value is headed
    for the database.
    """
    return datetime.now(UTC).replace(tzinfo=None)


def as_naive_utc(value: datetime) -> datetime:
    """Normalize *value* to the naive UTC form this layer stores.

    An aware datetime is converted to UTC and stripped of its tzinfo. A
    naive datetime is assumed to already be UTC and returned unchanged,
    which is the documented contract for callers rather than a guess —
    there is no way to detect the intended zone of a naive value, so
    the layer states its expectation and normalizes everything aware.

    Args:
        value: Any datetime, aware or naive.

    Returns:
        An equivalent naive UTC datetime.
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)
