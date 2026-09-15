"""Command-line entry points.

Two console scripts are registered in ``pyproject.toml`` and both land
here:

- ``vacantes`` → :func:`vacantes.cli.main.main`, the dispatcher that
  routes to the ``integrate`` and ``batch`` subcommands.
- ``job-agent-lab`` → :func:`vacantes.cli.integrate.main`, a frozen
  alias that bypasses the dispatcher entirely. Pointing it at the
  integrate module's own ``main`` is what guarantees the pre-rename
  argument surface the ``integrate-company`` skill drives.

Each subcommand module owns three public names — a description string,
an ``add_*_arguments`` function, and an ``execute`` function taking a
parsed namespace. :mod:`vacantes.cli.main` composes them without
knowing what any subcommand does, so a fourth entry point is a table
row rather than a new branch.

This package is the only layer allowed to depend on every other: it
parses argv, then hands off. It holds no extraction, persistence, or
scheduling logic of its own.
"""
