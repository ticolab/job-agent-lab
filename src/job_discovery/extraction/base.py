"""Strategy port: :class:`ExtractionStrategy` protocol, run context, registry.

This module owns the extraction-layer surface that ``cli.py`` dispatches
against and that adapter modules (``extraction.dom``, ``extraction.ats.*``)
plug into. Three surfaces live here:

- :class:`RunContext` — a frozen bundle of the per-run knobs the CLI
  passes to every strategy: the LLM model identifier, headless flag,
  agent step cap, and the :class:`~job_discovery.domain.region.TargetRegion`
  the run is scoped to. API strategies ignore ``model`` / ``headless`` /
  ``max_steps`` (they carry ``None`` semantics for those fields in the
  output report), but every strategy needs ``region`` to decide which
  postings to keep.

- :class:`ExtractionStrategy` — a structural protocol strategies satisfy
  by declaring a ``name: ClassVar[str]`` matching their registry key and
  an ``async extract(company, ctx) -> dict[str, Any]`` that returns the
  same report shape (see :func:`build_report`).

- :data:`STRATEGIES` + :func:`get_strategy` — the registry the CLI
  dispatches through. Registration happens in
  :mod:`job_discovery.extraction`'s ``__init__.py`` at package-load
  time. ``get_strategy`` raises ``ValueError`` for unknown names (belt
  behind the ``StrategyName`` ``Literal`` on
  :class:`~job_discovery.domain.company.Company`).

:func:`build_report` is the *single* author of the on-disk / print
report shape. Both :class:`~job_discovery.extraction.dom.strategy.DomStrategy`
and the API adapters call it — this keeps ``metadata.strategy`` a
uniform additive key across strategies and makes future report-shape
changes a one-file diff.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

from job_discovery.domain.company import Company
from job_discovery.domain.region import TargetRegion


@dataclass(frozen=True)
class RunContext:
    """Per-run knobs passed uniformly to every :class:`ExtractionStrategy`.

    Attributes:
        model: OpenAI model identifier. Consumed by the DOM strategy's
            agent; API strategies (Greenhouse, future ATS adapters)
            record ``None`` in their report's ``metadata.model``.
        headless: Whether the browser session runs headless. API
            strategies ignore this.
        max_steps: Upper bound on browser-use agent steps. API
            strategies ignore this.
        region: The :class:`TargetRegion` the run is scoped to. The DOM
            strategy renders its ``filter_tokens`` into ``GOAL_PROMPT``
            (via
            :func:`job_discovery.extraction.dom.agent.prompt.build_goal_prompt`);
            API strategies use its :meth:`~TargetRegion.matches`
            predicate to filter payloads.
    """

    model: str
    headless: bool
    max_steps: int
    region: TargetRegion


class ExtractionStrategy(Protocol):
    """Structural protocol every strategy implements.

    Duck-typed: strategies do not need to inherit from this class, they
    just need to expose the two attributes below. The ``name``
    attribute must match the key the strategy is registered under in
    :data:`STRATEGIES` and the string value of the corresponding
    ``StrategyName`` literal on :class:`Company`.
    """

    name: ClassVar[str]

    async def extract(self, company: Company, ctx: RunContext) -> dict[str, Any]:
        """Run the strategy against ``company`` and return a report dict.

        The report shape is authored by :func:`build_report`; strategies
        must not construct the top-level dict directly.
        """
        ...


def build_report(
    *,
    strategy: str,
    company_name: str,
    company_url: str,
    jobs: list[str],
    elapsed: float,
    model: str | None,
    agent_steps: int | None,
    agent_completed: bool | None,
    agent_had_errors: bool | None,
    error: str | None,
    states_visited: int | None = None,
) -> dict[str, Any]:
    """Build the extraction report dict (single author of the shape).

    The report is the on-disk JSON payload
    :func:`job_discovery.reporting.output.save_result` writes and the
    input :func:`~job_discovery.reporting.output.print_summary`
    renders. ``metadata.strategy`` is present on every run.
    ``metadata.states_visited`` is included only when the
    ``states_visited`` argument is not ``None``. Every single-state
    caller passes ``None`` (the default), so the key is absent from
    those dicts. The agent-less multi-state
    ``DomStrategy._extract_prefiltered`` path is the sole current
    producer of a non-``None`` value; it passes
    ``len(company.pre_filter_urls)``.

    API strategies (Greenhouse and future siblings) pass ``None`` for
    the agent fields (``model``, ``agent_steps``, ``agent_completed``,
    ``agent_had_errors``) — they do not run an LLM agent and reporting
    honest nulls is more informative than fabricated zeros.
    ``print_summary`` renders each ``None`` as ``n/a``.

    Args:
        strategy: The strategy name (``"dom"`` or ``"greenhouse"``).
        company_name: The company's canonical display name.
        company_url: The company's board URL (``job_board_url``).
        jobs: The extracted job posting URLs, in the order the strategy
            produced them. Passed through unchanged to the ``jobs``
            top-level key.
        elapsed: Wall-clock extraction time in seconds; rounded to 2
            decimal places in the report.
        model: LLM model identifier used, or ``None`` for API strategies.
        agent_steps: Number of browser-use agent steps taken, or ``None``
            for API strategies.
        agent_completed: Whether the agent reported ``is_done``, or
            ``None`` for API strategies.
        agent_had_errors: Whether the agent's history recorded any
            errors, or ``None`` for API strategies.
        error: The extraction-terminating error string, if any.
        states_visited: additive metadata for the agent-less
            multi-state union path. ``None`` (the default) omits the
            key entirely from ``metadata`` for byte-identical parity
            with single-state reports. ``DomStrategy._extract_prefiltered``
            is the sole current producer and passes
            ``len(company.pre_filter_urls)`` — even on the error path,
            the value reflects the declared state count, not the
            successfully-completed count.

    Returns:
        The report dict, ready for JSON serialisation.
    """
    metadata: dict[str, Any] = {
        "strategy": strategy,
        "model": model,
        "total_jobs_found": len(jobs),
        "extraction_time_seconds": round(elapsed, 2),
        "agent_steps": agent_steps,
        "agent_completed": agent_completed,
        "agent_had_errors": agent_had_errors,
        "error": error,
    }
    if states_visited is not None:
        metadata["states_visited"] = states_visited
    return {
        "company": company_name,
        "url": company_url,
        "jobs": jobs,
        "metadata": metadata,
    }


# Populated by :mod:`job_discovery.extraction`'s package ``__init__``
# at import time — each strategy registers itself there so the mapping
# is complete before any :func:`get_strategy` call. Keeping the mutation
# centralised (rather than each strategy module registering itself
# on import) keeps the strategy modules importable in isolation and
# avoids side-effect-on-import surprises.
STRATEGIES: dict[str, ExtractionStrategy] = {}


def get_strategy(name: str) -> ExtractionStrategy:
    """Look up a registered strategy by name.

    Raises ``ValueError`` for unknown names. The ``StrategyName``
    ``Literal`` on :class:`Company` already blocks unknown names at
    schema-validation time; this dispatcher rejects them defensively
    so a bare ``get_strategy(some_string)`` call from a test or script
    also fails loudly rather than returning ``None`` or ``KeyError``.
    """
    try:
        return STRATEGIES[name]
    except KeyError as exc:
        registered = ", ".join(sorted(STRATEGIES)) or "<none>"
        raise ValueError(
            f"Unknown strategy {name!r}. Registered: {registered}."
        ) from exc
