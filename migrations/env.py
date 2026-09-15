import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from vacantes.persistence.engine import database_url
from vacantes.persistence.models import Base
from vacantes.settings import DATABASE_PATH

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
#
# `disable_existing_loggers=False` is a correction to Alembic's stock
# template, not a preference. The default is True, which disables every
# logger that already exists — so invoking a migration in-process
# silently switches off all `vacantes` logging for the rest of that
# process. Two unrelated `caplog` tests caught it; an operator running a
# migration before a batch would have seen the batch's warnings vanish.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Autogenerate target. Every model must be reachable from this
# metadata or it is invisible to `alembic revision --autogenerate`.
target_metadata = Base.metadata

# The database location comes from `vacantes.settings` rather than from
# `sqlalchemy.url` in alembic.ini: two sources for one path is how a
# migration ends up applied to a different file than the application
# reads. alembic.ini therefore leaves the URL empty and this fills it.
# `vacantes batch --database PATH` has no counterpart here; the
# `VACANTES_DB` environment variable, read by `vacantes.settings`, is
# how a migration is pointed at the same non-default file.
#
# Conditional rather than unconditional so an explicit value still wins,
# which is what lets a test apply the migrations to a temporary database
# instead of the operator's real one.
if not config.get_main_option("sqlalchemy.url", ""):
    # The driver will not create the directory holding the SQLite file,
    # so a fresh checkout would fail before any migration ran. Scoped to
    # this branch so pointing the migrations elsewhere does not create
    # the operator's data directory as a side effect.
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.set_main_option("sqlalchemy.url", database_url(DATABASE_PATH))

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    # `render_as_batch` is required for SQLite: it cannot ALTER a column
    # or drop a constraint in place, so Alembic emits a rebuild through
    # a temporary table instead. Without it, the first migration that
    # changes an existing column fails at runtime rather than at review.
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
