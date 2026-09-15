"""Batch policy: cost classification, ceilings, and the skip decision.

This module owns every *decision* a batch makes that is not "run this
company". Three surfaces live here:

- :func:`concurrency_class` — which semaphore a company's run belongs
  to, classified by **cost** rather than by strategy name.
- :class:`BatchPolicy` — the frozen bundle of tunables one batch runs
  under: the two ceilings, the freshness window, the per-company
  timeout, and the ``force`` override.
- :func:`should_skip` — whether a company has already been done
  recently enough to leave alone.

Keeping classification here rather than on the
:class:`~vacantes.extraction.base.ExtractionStrategy` protocol is
deliberate. Widening that protocol would make every existing strategy
non-conforming, and it would hand a strategy knowledge of the scheduler
that :mod:`tests.unit.test_layering` exists to deny it. An eighth
adapter registers in ``STRATEGIES`` exactly as it does today and is
classified by one line here, which a test enforces.

The defaults live in this module rather than in
:mod:`vacantes.settings` because they are policy, not shared runtime
configuration: nothing outside a batch has an opinion about how many
browsers may run at once.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from vacantes.domain.company import Company
from vacantes.persistence import runs_repo
from vacantes.persistence.timestamps import utc_now

ConcurrencyClass = Literal["browser", "http"]

# Four browser runs and twenty HTTP runs. The two profiles differ by an
# order of magnitude: a browser run holds a Chromium context (memory)
# and, for ``dom``, an LLM agent (provider rate limits); an HTTP run is
# a JSON call bounded only by politeness. Tuned against observed
# behaviour in Phase 4, not derived from first principles.
DEFAULT_BROWSER_CONCURRENCY = 4
DEFAULT_HTTP_CONCURRENCY = 20

# Shorter than a day so a batch run at the same hour on consecutive days
# never skips itself, and long enough that two runs in one afternoon do
# not repeat work.
DEFAULT_FRESHNESS = timedelta(hours=20)

# A ceiling, not an expectation: the slowest observed board finishes
# well inside this. Its real job is to stop one hung Playwright context
# from holding a browser slot for the length of the batch.
DEFAULT_TIMEOUT = timedelta(minutes=10)


def concurrency_class(company: Company) -> ConcurrencyClass:
    """Classify *company*'s run by the resource it actually consumes.

    Classification is by **cost**, which is why this takes a
    :class:`Company` and not a strategy name. Splitting on the name
    alone gets exactly one case wrong: a ``coveo`` company with
    ``browser_token_key`` set opens a real
    :class:`~browser_use.BrowserSession` to read the JWT its own page
    minted into ``sessionStorage`` (the C19 closure), so it costs a
    Chromium context despite being an API strategy. Twenty of those in
    the HTTP bucket would exhaust memory.

    The ``strategy == "coveo"`` half of the second condition is for the
    reader and the type checker rather than for a reachable state — the
    schema validator on :class:`~vacantes.domain.company.CoveoConfig`
    already ties that config to its strategy.

    Returns:
        ``"browser"`` for anything that launches Chromium, ``"http"``
        for anything that is a plain HTTP call.
    """
    if company.strategy == "dom":
        return "browser"
    if (
        company.strategy == "coveo"
        and company.coveo is not None
        and company.coveo.browser_token_key is not None
    ):
        return "browser"
    return "http"


@dataclass(frozen=True)
class BatchPolicy:
    """The tunables one batch runs under.

    Frozen and passed down rather than read from module state, so a test
    can run a batch under a one-second timeout or a zero-length
    freshness window without monkeypatching anything.

    Attributes:
        browser_concurrency: Ceiling on simultaneous ``browser``-class
            runs.
        http_concurrency: Ceiling on simultaneous ``http``-class runs.
        freshness: How recently a company must have *succeeded* to be
            skipped. Ignored when ``force`` is set.
        timeout: Per-company wall-clock ceiling. Also the staleness
            bound the scheduler reaps abandoned runs against, since no
            run may legitimately exceed it.
        force: Run every company regardless of freshness.
    """

    browser_concurrency: int = DEFAULT_BROWSER_CONCURRENCY
    http_concurrency: int = DEFAULT_HTTP_CONCURRENCY
    freshness: timedelta = DEFAULT_FRESHNESS
    timeout: timedelta = DEFAULT_TIMEOUT
    force: bool = False


async def should_skip(
    session: AsyncSession, *, company: Company, policy: BatchPolicy
) -> bool:
    """Whether *company* was already done recently enough to leave alone.

    Delegates the predicate itself to
    :func:`~vacantes.persistence.runs_repo.has_fresh_success`, which
    counts **successes only**. That is the load-bearing half: a board
    that failed this morning stays eligible this afternoon, while one
    that succeeded is not repeated inside the window. This function owns
    only the arithmetic that turns a window into a cutoff, so there is
    one place where "fresh" is defined.

    Crash recovery falls out of the same rule. A killed batch leaves
    ``in_progress`` rows; the scheduler reaps them to ``failed`` at
    startup; they are therefore not successes, and the companies they
    belong to run again. No checkpoint file is involved.
    """
    if policy.force:
        return False
    return await runs_repo.has_fresh_success(
        session,
        company_slug=company.slug,
        since=utc_now() - policy.freshness,
    )
