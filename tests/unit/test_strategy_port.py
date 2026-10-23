"""Unit tests for the strategy port.

Covers three surfaces of :mod:`job_discovery.extraction.base`:

- Registry completeness — every ``StrategyName`` literal member has a
  registered implementation, and vice versa. Adding a strategy means
  extending both places; if a future contributor forgets one side, this
  test catches it before dispatch time.
- ``get_strategy`` — round-trip lookup for every registered name plus a
  ``ValueError`` on an unknown name (belt behind the ``Literal`` on
  ``Company.strategy``).
- ``build_report`` — the single author of the report shape. Two shape
  variants are exercised: the DOM shape (all agent fields populated),
  and the API shape (all agent fields ``None``). Both must carry
  ``metadata.strategy`` and preserve the exact top-level / metadata key
  sets, since ``print_summary`` and ``save_result`` consume this shape.
"""

from __future__ import annotations

from typing import get_args

import pytest

import job_discovery.extraction  # noqa: F401  — triggers strategy registration
from job_discovery.domain.company import StrategyName
from job_discovery.extraction.ats.coveo import CoveoStrategy
from job_discovery.extraction.ats.greenhouse import GreenhouseStrategy
from job_discovery.extraction.ats.phenom import PhenomStrategy
from job_discovery.extraction.ats.talentbrew import TalentbrewStrategy
from job_discovery.extraction.base import (
    STRATEGIES,
    RunContext,
    build_report,
    get_strategy,
)
from job_discovery.extraction.dom.strategy import DomStrategy

# Keys the report shape is contractually required to expose. Locked at
# module scope so a shape drift shows up as one obvious diff rather than
# scattered assertion failures. ``save_result`` and ``print_summary``
# both depend on the exact key sets below.
_EXPECTED_TOP_LEVEL: frozenset[str] = frozenset({"company", "url", "jobs", "metadata"})
_EXPECTED_METADATA: frozenset[str] = frozenset(
    {
        "strategy",
        "model",
        "total_jobs_found",
        "extraction_time_seconds",
        "agent_steps",
        "agent_completed",
        "agent_had_errors",
        "error",
    }
)


class TestRegistryCompleteness:
    """The ``StrategyName`` literal and :data:`STRATEGIES` must agree."""

    def test_every_literal_member_has_a_registered_strategy(self) -> None:
        assert set(STRATEGIES) == set(get_args(StrategyName))

    def test_registered_names_match_class_attributes(self) -> None:
        # The ``name`` attribute on each strategy class must match the
        # key it is registered under — otherwise ``get_strategy(name)``
        # would return an instance whose ``.name`` disagrees with the
        # lookup key, breaking any downstream logging that reads the
        # attribute instead of the key.
        for key, strategy in STRATEGIES.items():
            assert strategy.name == key

    def test_every_strategy_class_is_registered(self) -> None:
        # Belt on the parity test — an explicit inventory guards against
        # a future refactor that renames a class without touching either
        # the literal or the registry.
        assert isinstance(STRATEGIES["dom"], DomStrategy)
        assert isinstance(STRATEGIES["greenhouse"], GreenhouseStrategy)
        assert isinstance(STRATEGIES["phenom"], PhenomStrategy)
        assert isinstance(STRATEGIES["talentbrew"], TalentbrewStrategy)
        assert isinstance(STRATEGIES["coveo"], CoveoStrategy)


class TestGetStrategy:
    """``get_strategy`` looks up by name and fails loudly on unknowns."""

    @pytest.mark.parametrize(
        "name", ["dom", "greenhouse", "phenom", "talentbrew", "coveo"]
    )
    def test_returns_the_registered_instance(self, name: str) -> None:
        assert get_strategy(name) is STRATEGIES[name]

    def test_unknown_name_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown strategy"):
            get_strategy("workday")

    def test_unknown_name_error_lists_registered(self) -> None:
        # The error message should name every registered strategy so
        # the caller can tell at a glance whether the mismatch is a
        # typo or a missing registration.
        with pytest.raises(ValueError, match="dom, greenhouse"):
            get_strategy("workday")


class TestBuildReportShape:
    """The report shape is invariant across strategies (modulo nulls)."""

    def test_dom_shape_populates_every_agent_field(self) -> None:
        report = build_report(
            strategy="dom",
            company_name="Acme",
            company_url="https://acme.test/careers",
            jobs=["https://acme.test/careers/1", "https://acme.test/careers/2"],
            elapsed=1.2345,
            model="gpt-4o-mini",
            agent_steps=7,
            agent_completed=True,
            agent_had_errors=False,
            error=None,
        )

        assert set(report) == _EXPECTED_TOP_LEVEL
        assert set(report["metadata"]) == _EXPECTED_METADATA

        assert report["company"] == "Acme"
        assert report["url"] == "https://acme.test/careers"
        assert report["jobs"] == [
            "https://acme.test/careers/1",
            "https://acme.test/careers/2",
        ]

        meta = report["metadata"]
        assert meta["strategy"] == "dom"
        assert meta["model"] == "gpt-4o-mini"
        assert meta["total_jobs_found"] == 2
        # ``elapsed`` is rounded to 2 decimal places — locked here so a
        # future formatting change is a deliberate contract change.
        assert meta["extraction_time_seconds"] == 1.23
        assert meta["agent_steps"] == 7
        assert meta["agent_completed"] is True
        assert meta["agent_had_errors"] is False
        assert meta["error"] is None
        assert "expected_jobs" not in meta
        assert "verdict" not in meta

    def test_api_shape_carries_honest_nulls(self) -> None:
        # Greenhouse and future API strategies don't run an LLM agent.
        # ``build_report`` must accept ``None`` for every agent field
        # and propagate it verbatim — ``print_summary`` renders each
        # ``None`` as ``n/a``, and ``save_result`` serialises it as
        # JSON ``null``.
        report = build_report(
            strategy="greenhouse",
            company_name="Zscaler",
            company_url="https://job-boards.greenhouse.io/zscaler",
            jobs=[],
            elapsed=0.5,
            model=None,
            agent_steps=None,
            agent_completed=None,
            agent_had_errors=None,
            error=None,
        )

        assert set(report) == _EXPECTED_TOP_LEVEL
        assert set(report["metadata"]) == _EXPECTED_METADATA

        meta = report["metadata"]
        assert meta["strategy"] == "greenhouse"
        assert meta["model"] is None
        assert meta["agent_steps"] is None
        assert meta["agent_completed"] is None
        assert meta["agent_had_errors"] is None
        assert meta["total_jobs_found"] == 0
        assert "expected_jobs" not in meta
        assert "verdict" not in meta

    def test_metadata_strategy_key_is_present_on_every_report(self) -> None:
        # The additive ``metadata.strategy`` key is the one deliberate
        # output-contract change. If it ever silently
        # disappears from ``build_report``, this test breaks first —
        # before ``print_summary`` starts KeyErroring in production.
        report = build_report(
            strategy="dom",
            company_name="X",
            company_url="u",
            jobs=[],
            elapsed=0.0,
            model="m",
            agent_steps=0,
            agent_completed=False,
            agent_had_errors=True,
            error="boom",
        )
        assert "strategy" in report["metadata"]

    def test_error_string_is_passed_through(self) -> None:
        report = build_report(
            strategy="dom",
            company_name="X",
            company_url="u",
            jobs=[],
            elapsed=0.0,
            model="m",
            agent_steps=0,
            agent_completed=False,
            agent_had_errors=True,
            error="timeout",
        )
        assert report["metadata"]["error"] == "timeout"


class TestRunContext:
    """``RunContext`` is a frozen dataclass — mutation raises."""

    def test_is_frozen(self) -> None:
        # Import inside the test to avoid a top-level cycle if the
        # region module ever grows a dependency on ``extraction``.
        from dataclasses import FrozenInstanceError

        from job_discovery.domain.region import COSTA_RICA_LATAM

        ctx = RunContext(
            model="gpt-4o-mini",
            headless=True,
            max_steps=30,
            region=COSTA_RICA_LATAM,
        )
        with pytest.raises(FrozenInstanceError):
            ctx.model = "other"  # type: ignore[misc]
