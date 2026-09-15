"""COMPONENT 3 — concurrency and the per-company unit of work.

Runs the corpus. The whole component is a semaphore and a gather, which
is the appropriate weight for one operator, one machine, and units of
work that have no dependencies on each other. A workflow framework is a
non-goal until there is cross-run durable state or distributed workers
to justify one; ``ARCHITECTURE.md`` ("Running a batch") records why.

Three modules, one responsibility each:

- :mod:`vacantes.batch.policy` — decides *whether and how expensively*:
  cost classification into the two concurrency classes, the ceilings and
  windows, and the skip rule.
- :mod:`vacantes.batch.worker` — decides *what one unit of work means*:
  the run row, the extraction, the persisted URL set, and the failure
  boundary that keeps one broken board from aborting the batch.
- :mod:`vacantes.batch.scheduler` — decides *when*: startup reaping,
  the catalog projection, fan-out under the semaphores, and aggregation.

This package imports ``domain``, ``extraction``, and ``persistence``,
and nothing else — ``tests/unit/test_layering.py`` enforces it. It
notably does not import ``catalog``: the scheduler receives the
companies to run as an argument, which is what lets a caller run a
slice, and lets a test run three fakes. Nor does it import a global
engine; the session factory is passed in.

The direction of that dependency is the point. ``extraction`` may reach
neither this package nor ``persistence``, so a strategy cannot tell
whether it was invoked by the integration CLI or by a scheduled batch —
which is what keeps a human-reviewed onboarding run a valid prediction
of what the scheduled run will do.
"""
