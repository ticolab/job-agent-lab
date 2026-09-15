"""COMPONENT 2 — the database: schema, engine, and repositories.

Owns every SQL statement in the system. The tables answer two distinct
questions and are kept separate for that reason: ``job_urls`` answers
*what is open*, and ``company_runs`` answers *did we successfully look,
and when*. A single ``updated_at`` column spanning both would conflate a
failed run with a successful one and silently break the retry semantics
the batch scheduler depends on — a board that failed this morning would
look "done" this afternoon and stop being retried.

The public surface is three repository modules plus the engine:

- :mod:`vacantes.persistence.engine` — the async engine, the SQLite
  pragmas that make concurrency safe, and the session factory.
- :mod:`vacantes.persistence.models` — the declarative schema.
- :mod:`vacantes.persistence.catalog_repo` — projects the catalog into
  the ``companies`` table so the database is self-describing.
- :mod:`vacantes.persistence.jobs_repo` — the atomic replace of a
  company's current URL set.
- :mod:`vacantes.persistence.runs_repo` — run lifecycle, freshness, and
  stale-run reaping.

No module outside this package imports a session, builds a query, or
opens a transaction. That gives transaction boundaries, retries, and
error translation exactly one home, and it makes the data layer
testable without a scheduler.

This package imports only ``domain`` and ``settings``, which
``tests/unit/test_layering.py`` enforces. In particular it does not
import ``catalog``: the catalog is the source of truth for the corpus
and this layer only ever *projects* it, receiving the companies to sync
as an argument rather than reaching for the global list.
"""
