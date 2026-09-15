"""Pure-domain types: the vocabulary every component speaks.

The domain layer owns the value objects that describe *what* the system
operates on (companies, link rules, extraction results). It intentionally
imports nothing from ``extraction``, ``persistence``, ``batch``, ``cli``,
``reporting``, or any browser/HTTP library — those layers depend on the
domain, never the other way around.

This is the innermost ring of the dependency graph, and being a leaf is
what lets the same types cross every boundary without dragging a browser
or a database session along with them. See ``ARCHITECTURE.md`` for the
dependency rule; ``tests/unit/test_layering.py`` enforces it.
"""
