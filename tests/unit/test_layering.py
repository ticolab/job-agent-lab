"""Enforce unit and package dependency direction across every source package."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
PACKAGE_ROOTS = {
    name: SRC / name
    for name in (
        "job_discovery",
        "job_eligibility",
        "job_extractor",
        "job_pipeline",
    )
}
ROOT_UNIT = "<root>"

ALLOWED_IMPORTS: dict[str, dict[str, frozenset[str]]] = {
    "job_discovery": {
        ROOT_UNIT: frozenset(),
        "settings": frozenset(),
        "domain": frozenset(),
        "catalog": frozenset({"domain"}),
        "extraction": frozenset({"domain", "settings"}),
        "reporting": frozenset({"catalog"}),
        "persistence": frozenset({"domain", "settings"}),
        "batch": frozenset({"domain", "extraction", "persistence"}),
        "cli": frozenset(
            {
                "batch",
                "catalog",
                "domain",
                "extraction",
                "persistence",
                "reporting",
                "settings",
            }
        ),
    },
    "job_eligibility": {
        ROOT_UNIT: frozenset({"prompt", "schema", "validate", "version"}),
        "prompt": frozenset(),
        "schema": frozenset(),
        "validate": frozenset({"schema", "version"}),
        "version": frozenset({"prompt", "schema"}),
    },
    "job_extractor": {
        ROOT_UNIT: frozenset({"normalize", "prompt", "schema", "validate", "version"}),
        "data": frozenset(),
        "normalize": frozenset({"schema"}),
        "prompt": frozenset(),
        "schema": frozenset(),
        "validate": frozenset({"normalize", "schema", "version"}),
        "version": frozenset({"prompt", "schema"}),
    },
    "job_pipeline": {
        ROOT_UNIT: frozenset(),
        "cli": frozenset(
            {
                ROOT_UNIT,
                "identity",
                "llm",
                "persistence",
                "policy",
                "reporting",
                "runner",
                "settings",
            }
        ),
        "content": frozenset({"identity", "settings"}),
        "identity": frozenset({"settings"}),
        "llm": frozenset(),
        "persistence": frozenset(),
        "policy": frozenset({"content", "llm", "settings"}),
        "reporting": frozenset({"persistence", "runner"}),
        "runner": frozenset({"content", "identity", "llm", "persistence", "policy"}),
        "settings": frozenset(),
    },
}

ALLOWED_PACKAGE_IMPORTS: dict[str, frozenset[str]] = {
    "job_discovery": frozenset(),
    "job_eligibility": frozenset(),
    "job_extractor": frozenset(),
    "job_pipeline": frozenset({"job_discovery", "job_eligibility", "job_extractor"}),
}

ALLOWED_DISCOVERY_IMPORTS_FOR_PIPELINE = frozenset(
    {"domain", "catalog", "persistence", "settings"}
)
FORBIDDEN_FOR_EXTRACTION = frozenset({"persistence", "batch"})
LEAF_PACKAGES = frozenset({"job_eligibility", "job_extractor"})


def _source_files(package: str) -> list[Path]:
    return sorted(PACKAGE_ROOTS[package].rglob("*.py"))


def _unit_of(package: str, path: Path) -> str:
    relative = path.relative_to(PACKAGE_ROOTS[package])
    if len(relative.parts) == 1:
        return ROOT_UNIT if relative.name == "__init__.py" else relative.stem
    return relative.parts[0]


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _absolute_imports(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module is not None
        ):
            modules.add(node.module)
    return modules


def _repo_imports(tree: ast.Module) -> set[str]:
    return {
        module
        for module in _absolute_imports(tree)
        if module.split(".")[0] in PACKAGE_ROOTS
    }


def _unit_from_import(module: str) -> str:
    parts = module.split(".")
    return parts[1] if len(parts) > 1 else ROOT_UNIT


@pytest.mark.parametrize("package", sorted(PACKAGE_ROOTS))
def test_source_tree_is_discoverable(package: str) -> None:
    assert PACKAGE_ROOTS[package].is_dir()
    assert _source_files(package)


@pytest.mark.parametrize("package", sorted(PACKAGE_ROOTS))
def test_every_unit_has_an_allowlist_entry(package: str) -> None:
    discovered = {_unit_of(package, path) for path in _source_files(package)}
    assert discovered == set(ALLOWED_IMPORTS[package])


def test_no_module_uses_a_relative_import() -> None:
    offenders: list[str] = []
    for package in PACKAGE_ROOTS:
        for path in _source_files(package):
            for node in ast.walk(_parse(path)):
                if isinstance(node, ast.ImportFrom) and node.level > 0:
                    offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert not offenders


def test_internal_imports_stay_within_the_allowlist() -> None:
    violations: list[str] = []
    for package in PACKAGE_ROOTS:
        for path in _source_files(package):
            unit = _unit_of(package, path)
            allowed = ALLOWED_IMPORTS[package].get(unit, frozenset())
            imported_units = {
                _unit_from_import(module)
                for module in _repo_imports(_parse(path))
                if module.split(".")[0] == package
            }
            for imported in sorted(imported_units - {unit}):
                if imported not in allowed:
                    violations.append(f"{path.relative_to(SRC)}: {unit} -> {imported}")
    assert not violations, "layering violations:\n  " + "\n  ".join(violations)


def test_cross_package_imports_stay_within_the_allowlist() -> None:
    violations: list[str] = []
    for package in PACKAGE_ROOTS:
        for path in _source_files(package):
            for module in sorted(_repo_imports(_parse(path))):
                imported_package = module.split(".")[0]
                if (
                    imported_package != package
                    and imported_package not in ALLOWED_PACKAGE_IMPORTS[package]
                ):
                    violations.append(
                        f"{path.relative_to(SRC)}: {package} -> {imported_package}"
                    )
    assert not violations, "package violations:\n  " + "\n  ".join(violations)


def test_pipeline_uses_only_approved_discovery_units() -> None:
    violations: list[str] = []
    for path in _source_files("job_pipeline"):
        for module in _repo_imports(_parse(path)):
            if module.split(".")[0] != "job_discovery":
                continue
            unit = _unit_from_import(module)
            if unit not in ALLOWED_DISCOVERY_IMPORTS_FOR_PIPELINE:
                violations.append(f"{path.relative_to(SRC)}: job_discovery.{unit}")
    assert not violations


@pytest.mark.parametrize("package", sorted(LEAF_PACKAGES))
def test_leaf_packages_import_nothing_from_repo(package: str) -> None:
    assert ALLOWED_PACKAGE_IMPORTS[package] == frozenset()
    assert not {
        module
        for path in _source_files(package)
        for module in _repo_imports(_parse(path))
        if module.split(".")[0] != package
    }


def test_discovery_imports_no_new_package() -> None:
    assert ALLOWED_PACKAGE_IMPORTS["job_discovery"] == frozenset()


@pytest.mark.parametrize("forbidden", sorted(FORBIDDEN_FOR_EXTRACTION))
def test_discovery_extraction_never_reaches_forbidden_units(forbidden: str) -> None:
    assert forbidden not in ALLOWED_IMPORTS["job_discovery"]["extraction"]
