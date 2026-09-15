"""Async engine, SQLite pragmas, and the session factory.

SQLite is sized correctly for this workload: roughly 250 companies with
tens of URLs each is low tens of thousands of rows, written a few times
a week by one process. A database server would add an operational
dependency that buys nothing, and backup is a file copy. SQLAlchemy is
used despite the modest schema so the eventual PostgreSQL move is a
connection-string change, and the async driver keeps database calls off
the event loop that is concurrently driving browser sessions.

Two of the four pragmas below are not tuning — they are correctness.
Without ``busy_timeout``, concurrent workers produce intermittent
``database is locked`` errors under a write lock that would have cleared
in milliseconds. Without ``foreign_keys``, SQLite silently ignores every
``REFERENCES`` clause in the schema and the ``ON DELETE CASCADE``
declarations become documentation.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from vacantes.settings import DATABASE_PATH

# Applied to every new connection, not once per engine: SQLite scopes
# `foreign_keys` and `busy_timeout` per connection, so setting them at
# engine creation would leave pooled connections unconfigured.
#
#   journal_mode = WAL       readers never block the writer
#   busy_timeout = 5000      wait on the write lock instead of erroring
#   synchronous  = NORMAL    durable enough for a regenerable dataset
#   foreign_keys = ON        SQLite disables FK enforcement by default
#
# `journal_mode` is persistent in the database file rather than
# per-connection, but is harmless to re-assert and is kept here so the
# full set reads as one unit.
_PRAGMAS: tuple[tuple[str, str], ...] = (
    ("journal_mode", "WAL"),
    ("busy_timeout", "5000"),
    ("synchronous", "NORMAL"),
    ("foreign_keys", "ON"),
)


def database_url(path: Path) -> str:
    """Build the aiosqlite driver URL for a database at *path*.

    The one place a connection URL is constructed, which is what makes
    the documented PostgreSQL migration a change in a single function.
    The path is resolved to absolute so the URL does not silently
    depend on the process's working directory at connect time.
    """
    return f"sqlite+aiosqlite:///{path.resolve()}"


def _apply_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
    """Apply :data:`_PRAGMAS` to a newly-opened SQLite connection.

    Registered as a ``connect`` listener rather than executed once after
    engine creation, because a connection pool opens connections lazily
    and each one starts from SQLite's defaults.

    ``dbapi_connection.cursor()`` is used directly and unconditionally.
    Under the aiosqlite driver this is not a :mod:`sqlite3` cursor but
    SQLAlchemy's ``AsyncAdapt`` facade, which runs the statement on the
    adapter's own event loop and is valid from inside this synchronous
    hook. An earlier version guarded on ``isinstance(..., sqlite3.
    Connection)`` and, because the adapter wraps an
    ``aiosqlite.Connection`` rather than a raw one, silently applied
    nothing at all — leaving WAL off and foreign keys unenforced.

    This function must therefore never skip quietly: a pragma that was
    not applied is invisible until concurrent writers start failing, so
    a genuine failure here should raise and stop the run.
    """
    cursor = dbapi_connection.cursor()
    try:
        for name, value in _PRAGMAS:
            cursor.execute(f"PRAGMA {name} = {value}")
    finally:
        cursor.close()


def create_engine(path: Path | None = None) -> AsyncEngine:
    """Create the async engine for the database at *path*.

    Creates the parent directory if it does not exist, so a fresh
    checkout needs no ``mkdir data`` step before the first run.

    Args:
        path: Database file location. Defaults to
            :data:`vacantes.settings.DATABASE_PATH`.

    Returns:
        An :class:`AsyncEngine` whose connections carry
        :data:`_PRAGMAS`. The caller owns it and must ``dispose()`` it;
        :func:`database` does that automatically.
    """
    resolved = DATABASE_PATH if path is None else path
    resolved.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(database_url(resolved))
    event.listen(engine.sync_engine, "connect", _apply_pragmas)
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build the session factory the repositories are called with.

    ``expire_on_commit=False`` because the repositories commit and then
    return values read from the committed objects. With the default
    ``True``, every such attribute access would trigger a lazy refresh
    against a closed transaction and raise.
    """
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def database(
    path: Path | None = None,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yield a session factory for *path*, disposing the engine on exit.

    The convenience entry point for a caller that owns the whole
    lifecycle — a test, a migration script, or an operator one-off. The
    batch scheduler will instead create one engine for the run and hand
    the factory to each worker, so workers receive their session factory
    rather than importing a global engine.
    """
    engine = create_engine(path)
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()
