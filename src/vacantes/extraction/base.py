"""Strategy port: :class:`ExtractionStrategy` protocol, run context, registry.

This module owns the extraction-layer surface that ``cli.py`` dispatches
against and that adapter modules (``extraction.dom``, ``extraction.ats.*``)
plug into. Three surfaces live here:

- :class:`RunContext` — a frozen bundle of the per-run knobs the CLI
  passes to every strategy: the LLM model identifier, headless flag,
  agent step cap, and the :class:`~vacantes.domain.region.TargetRegion`
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
  :mod:`vacantes.extraction`'s ``__init__.py`` at package-load
  time. ``get_strategy`` raises ``ValueError`` for unknown names (belt
  behind the ``StrategyName`` ``Literal`` on
  :class:`~vacantes.domain.company.Company`).

:func:`build_report` is the *single* author of the on-disk / print
report shape. Both :class:`~vacantes.extraction.dom.strategy.DomStrategy`
and the API adapters call it — this keeps ``metadata.strategy`` a
uniform additive key across strategies and makes future report-shape
changes a one-file diff.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

from vacantes.domain.company import Company
from vacantes.domain.region import TargetRegion


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
            :func:`vacantes.extraction.dom.agent.prompt.build_goal_prompt`);
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


def compute_verdict(found: int, expected: int | None) -> str:
    """Classify a run's ``total_jobs_found`` against its human target.

    Returns one of the four ``metadata.verdict`` values:

    - ``"unverified"`` when ``expected is None`` — the catalog entry has
      never been counted, so no judgement is possible. This is the
      state every pre-SYS-9 catalog entry lands in (no backfill).
    - ``"match"`` when ``found == expected``.
    - ``"under"`` when ``found < expected``.
    - ``"over"`` when ``found > expected``.

    Two edge cases follow directly from those rules and are worth
    calling out because ``0`` is a legitimate ``expected_jobs`` value
    for a Case-C board (a location filter exists but has no options
    matching the target region):

    - ``expected=0, found=0`` → ``"match"``. A Case-C board honestly
      reporting zero listings is a passing run.
    - ``expected=0, found>0`` → ``"over"``. A Case-C board that
      suddenly returns listings deserves the same "delta from human
      count" attention as any other overshoot; the CLI's ``--strict``
      flag treats it accordingly.

    Args:
        found: The value :func:`build_report` puts into
            ``metadata.total_jobs_found`` (``len(jobs)`` at the call
            site).
        expected: The company's ``expected_jobs`` field, passed
            through unchanged from the ``Company`` model. Never
            adjusted to match observed reality — expectations move
            through :file:`new-companies.json` and a fresh integrate-
            company run, not through this function.
    """
    if expected is None:
        return "unverified"
    if found == expected:
        return "match"
    if found < expected:
        return "under"
    return "over"


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
    expected_jobs: int | None,
    states_visited: int | None = None,
) -> dict[str, Any]:
    """Build the extraction report dict (single author of the shape).

    The report is the on-disk JSON payload
    :func:`vacantes.reporting.output.save_result` writes and the
    input :func:`~vacantes.reporting.output.print_summary`
    renders. The shape is byte-compatible with the pre-SYS-4
    ``_build_output`` dict *modulo three additive-key rounds*:

    - SYS-4: ``metadata.strategy`` (present on every run).
    - SYS-9: ``metadata.expected_jobs`` and ``metadata.verdict``,
      appended *after* ``metadata.error`` so the serialisation order
      of every pre-SYS-9 key is byte-identical.
    - SYS-13: ``metadata.states_visited``, appended *after*
      ``metadata.verdict`` **only when the ``states_visited`` argument
      is not ``None``**. Every pre-SYS-13 caller passes ``None`` (the
      default) and their reports stay byte-identical — the key is
      absent from those dicts, not present-with-null. The agent-less
      multi-state ``DomStrategy._extract_prefiltered`` path (SYS-13)
      is the sole current producer of a non-``None`` value; it passes
      ``len(company.pre_filter_urls)``.

    API strategies (Greenhouse and future siblings) pass ``None`` for
    the agent fields (``model``, ``agent_steps``, ``agent_completed``,
    ``agent_had_errors``) — they do not run an LLM agent and reporting
    honest nulls is more informative than fabricated zeros.
    ``print_summary`` renders each ``None`` as ``n/a``.

    The ``expected_jobs`` argument is *always* the value of the
    ``Company.expected_jobs`` field for the run — strategies pass it
    straight through and never mutate it. Verdict classification is
    centralised in :func:`compute_verdict`; strategies never compute
    verdicts themselves.

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
        expected_jobs: The company's human-counted, region-filtered
            target (``Company.expected_jobs``). ``None`` when the
            company has never been counted, in which case the emitted
            verdict is ``"unverified"``.
        states_visited: SYS-13 additive metadata for the agent-less
            multi-state union path. ``None`` (the default) omits the
            key entirely from ``metadata`` for byte-identical parity
            with pre-SYS-13 reports; an integer value is appended to
            ``metadata`` after ``verdict``. ``DomStrategy._extract_prefiltered``
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
        "expected_jobs": expected_jobs,
        "verdict": compute_verdict(len(jobs), expected_jobs),
    }
    # Conditional additive key (SYS-13). Every pre-SYS-13 caller passes
    # states_visited=None (the default) and the key is absent from their
    # metadata — this is a load-bearing byte-stability property for the
    # agent + Greenhouse report shapes, honoured by JSON serialisation
    # and every existing consumer (print_summary, save_result, --strict).
    if states_visited is not None:
        metadata["states_visited"] = states_visited
    return {
        "company": company_name,
        "url": company_url,
        "jobs": jobs,
        "metadata": metadata,
    }


# Populated by :mod:`vacantes.extraction`'s package ``__init__``
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
