"""Enforce the package layering rule by parsing the real import graph.

``ARCHITECTURE.md`` states that dependencies point inward and never
cycle. Until this test existed, nothing enforced it — the graph was
clean by review discipline alone, which is exactly the kind of property
that decays silently as packages are added.

Why it is load-bearing rather than tidy: two entry points share one
extraction core. The integration CLI drives one board at a time and
produces the JSON artifact a human reviews before a catalog entry is
committed; a batch scheduler runs the corpus concurrently and persists
the result. Both call the same ``extract`` coroutine. That makes the
integration workflow a correctness signal for scheduled runs — but only
while a strategy cannot tell which caller invoked it. A strategy able to
reach the database, or to detect that a batch is in progress, could
behave differently under the scheduler than under the CLI, and the
onboarding evidence would stop meaning anything.

Three properties are asserted:

- Every cross-package import is in :data:`ALLOWED_IMPORTS`.
- Every top-level unit under ``src/vacantes/`` has an entry in
  :data:`ALLOWED_IMPORTS`, so adding a package forces an explicit
  layering decision instead of defaulting to unconstrained.
- No module uses a relative import, which would otherwise bypass the
  absolute-path matching this test relies on.

:data:`ALLOWED_IMPORTS` is written from the **actual** graph, not from
intent, and may only ever shrink. An entry that is wider than reality
would silently license the very edge the rule exists to forbid.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE_NAME = "vacantes"
SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / PACKAGE_NAME

# Sentinel for ``src/vacantes/__init__.py``, which belongs to no
# subpackage and must stay free of intra-project imports so that
# importing the distribution costs nothing.
ROOT_UNIT = "<root>"

# Cross-package edges only. A unit importing itself is an intra-package
# concern, not a layering one, so self-edges are always permitted and
# deliberately absent here — which lets this table be read directly
# against the dependency graph in ``ARCHITECTURE.md``.
ALLOWED_IMPORTS: dict[str, frozenset[str]] = {
    ROOT_UNIT: frozenset(),
    "settings": frozenset(),
    "domain": frozenset(),
    "catalog": frozenset({"domain"}),
    "extraction": frozenset({"domain", "settings"}),
    "reporting": frozenset({"catalog"}),
    # Notably absent: ``catalog``. The persistence layer projects the
    # corpus but never owns it, so ``sync_catalog`` receives the
    # companies as an argument instead of importing ``COMPANIES``. That
    # is also what keeps this row to two entries.
    "persistence": frozenset({"domain", "settings"}),
    # Narrower than TRANSITION.md §2.3 anticipated, for the same reason
    # as the row above: the scheduler is handed the companies to run and
    # the session factory to use, so it reaches for neither ``catalog``
    # nor a global engine. ``settings`` is absent too — the ceilings and
    # windows are policy and live in ``batch/policy.py``. ``reporting``
    # belongs to the CLI that renders a batch, not to the batch itself.
    "batch": frozenset({"domain", "extraction", "persistence"}),
    "cli": frozenset({"catalog", "domain", "extraction", "reporting", "settings"}),
}

# The invariant that matters most, asserted by name as well as by the
# table above so it survives a careless allowlist edit: a strategy can
# reach neither the database nor the scheduler. Deliberately not tied to
# which packages have landed — ``persistence`` exists now and is still
# listed, because the rule is about direction, not about readiness.
FORBIDDEN_FOR_EXTRACTION = frozenset({"persistence", "batch"})


def _source_files() -> list[Path]:
    """Return every Python module shipped under ``src/vacantes/``."""
    return sorted(SRC_ROOT.rglob("*.py"))


def _unit_of(path: Path) -> str:
    """Return the top-level layering unit that owns *path*.

    ``settings.py`` is a top-level module rather than a package, so it
    is its own unit; ``__init__.py`` at the root maps to
    :data:`ROOT_UNIT`. Everything else is attributed to its first path
    component, which is the granularity the dependency rule is stated
    at.
    """
    relative = path.relative_to(SRC_ROOT)
    if len(relative.parts) == 1:
        return ROOT_UNIT if relative.name == "__init__.py" else relative.stem
    return relative.parts[0]


def _imported_modules(tree: ast.Module) -> set[str]:
    """Return the dotted names *tree* imports from this package.

    Covers both ``import vacantes.x.y`` and ``from vacantes.x import y``.
    Relative imports are ignored here and rejected outright by
    :class:`TestNoRelativeImports`, so they cannot slip past.
    """
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(
                alias.name
                for alias in node.names
                if alias.name.split(".")[0] == PACKAGE_NAME
            )
        elif (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module is not None
            and node.module.split(".")[0] == PACKAGE_NAME
        ):
            modules.add(node.module)
    return modules


def _imported_units(tree: ast.Module) -> set[str]:
    """Return the layering units *tree* imports, excluding self-edges."""
    units: set[str] = set()
    for module in _imported_modules(tree):
        parts = module.split(".")
        if len(parts) < 2:
            # A bare ``import vacantes`` targets the root unit.
            units.add(ROOT_UNIT)
        else:
            units.add(parts[1])
    return units


def _parse(path: Path) -> ast.Module:
    """Parse *path* into an AST, naming the file on syntax errors."""
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


class TestAllowlistCoverage:
    """The table must describe the tree, not a subset of it."""

    def test_source_tree_is_discoverable(self) -> None:
        """Guard against a silently-empty scan after a layout change."""
        assert SRC_ROOT.is_dir(), f"package root not found: {SRC_ROOT}"
        assert _source_files(), f"no modules found under {SRC_ROOT}"

    def test_every_unit_has_an_allowlist_entry(self) -> None:
        """A new package must make an explicit layering decision.

        Without this, adding ``src/vacantes/persistence/`` would ship
        with no constraints at all and the rule would quietly stop
        covering the newest code — the moment it matters most.
        """
        discovered = {_unit_of(path) for path in _source_files()}
        assert discovered == set(ALLOWED_IMPORTS), (
            "ALLOWED_IMPORTS is out of sync with the tree: "
            f"missing entries for {sorted(discovered - set(ALLOWED_IMPORTS))}, "
            f"stale entries for {sorted(set(ALLOWED_IMPORTS) - discovered)}"
        )


class TestNoRelativeImports:
    """Relative imports would bypass this test's matching entirely."""

    def test_no_module_uses_a_relative_import(self) -> None:
        offenders: list[str] = []
        for path in _source_files():
            for node in ast.walk(_parse(path)):
                if isinstance(node, ast.ImportFrom) and node.level > 0:
                    offenders.append(f"{path.relative_to(SRC_ROOT)}:{node.lineno}")
        assert not offenders, (
            "relative imports are not allowed because they cannot be "
            f"attributed to a layering unit: {offenders}"
        )


class TestDependencyDirection:
    """Every cross-package edge must appear in the allowlist."""

    def test_imports_stay_within_the_allowlist(self) -> None:
        violations: list[str] = []
        for path in _source_files():
            unit = _unit_of(path)
            # A unit absent from the table is allowed nothing rather than
            # everything. ``TestAllowlistCoverage`` reports the missing
            # entry itself; defaulting here keeps this test explaining
            # the edge instead of raising ``KeyError``.
            allowed = ALLOWED_IMPORTS.get(unit, frozenset())
            for imported in sorted(_imported_units(_parse(path)) - {unit}):
                if imported not in allowed:
                    violations.append(
                        f"{path.relative_to(SRC_ROOT)}: "
                        f"{unit} -> {imported} (allowed: {sorted(allowed) or 'none'})"
                    )
        report = "\n  ".join(violations)
        assert not violations, f"layering violations:\n  {report}"

    @pytest.mark.parametrize("forbidden", sorted(FORBIDDEN_FOR_EXTRACTION))
    def test_extraction_never_reaches_persistence_or_batch(
        self, forbidden: str
    ) -> None:
        """The invariant that keeps onboarding evidence meaningful.

        A strategy that could reach the database, or detect that a batch
        is in progress, could behave differently under the scheduler than
        under the integration CLI — and the JSON artifact a human reviews
        before committing a catalog entry would stop predicting what the
        scheduled run does.

        Asserted by name as well as by table so the intent survives a
        careless edit to :data:`ALLOWED_IMPORTS`.
        """
        assert forbidden not in ALLOWED_IMPORTS["extraction"]

    def test_domain_is_a_leaf(self) -> None:
        """The kernel's innermost ring imports nothing from the package.

        This is what lets the same types cross every boundary without
        dragging a browser or a database session along with them.
        """
        assert ALLOWED_IMPORTS["domain"] == frozenset()
