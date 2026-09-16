"""DOM-based extraction strategy: one company -> one output dict.

:class:`DomStrategy` is the browser-use / deterministic-matcher pipeline
wrapped in the :class:`~vacantes.extraction.base.ExtractionStrategy`
protocol so the CLI can dispatch through
:func:`~vacantes.extraction.base.get_strategy` uniformly with the
API strategies (:class:`~vacantes.extraction.ats.greenhouse.GreenhouseStrategy`
and future siblings).

The heavy lifting is unchanged from legacy:

- :func:`~vacantes.extraction.dom.agent.controller.build_controller` — the two
  custom tools the agent uses (``extract_job_links``,
  ``report_no_matching_location_filter``).
- :func:`~vacantes.extraction.dom.agent.runner.build_agent` — the
  ``Agent`` + ``BrowserSession`` wiring (LLM, browser profile, task
  prompt).
- :class:`~vacantes.domain.results.ExtractionResult` — pydantic
  parse of the agent's final JSON payload.

The two private helpers (``_parse_result``, ``_build_output``) stay
private to this module: the parse-and-shape glue between ``browser-use``
(which returns a free-form string in ``history.final_result()``) and the
uniform report shape authored by
:func:`~vacantes.extraction.base.build_report`. ``_build_output``
now delegates to ``build_report`` rather than authoring the dict inline,
so the ``metadata.strategy`` additive key stays consistent across
strategies.

``extract_jobs`` is kept as a free-function facade for backwards
compatibility with scripts and internal callers that predate the
strategy port — :class:`DomStrategy` is a thin wrapper that maps
``Company`` + ``RunContext`` onto ``extract_jobs``'s keyword arguments.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, ClassVar
from urllib.parse import urlparse

from browser_use.agent.views import AgentHistoryList
from browser_use.browser.profile import BrowserProfile
from browser_use.browser.session import BrowserSession

from vacantes.domain.company import Company, RuntimeHooks
from vacantes.domain.results import ExtractionResult
from vacantes.extraction.base import RunContext, build_report
from vacantes.extraction.dom.collector import collect_job_links
from vacantes.extraction.dom.rules import derive_path_prefix
from vacantes.settings import (
    DEFAULT_MAX_STEPS,
    DEFAULT_MODEL,
    RENDER_SCROLL_COUNT,
    RENDER_WAIT_SEC,
    plausible_headless_ua,
)

logger = logging.getLogger(__name__)


class DomStrategy:
    """browser-use agent + deterministic DOM matcher strategy."""

    name: ClassVar[str] = "dom"

    async def extract(self, company: Company, ctx: RunContext) -> dict[str, Any]:
        """Run the DOM strategy against ``company``.

        Maps ``company``'s identity + link-rule fields and ``ctx``'s
        runtime knobs onto :func:`extract_jobs`. The
        :class:`~vacantes.domain.region.TargetRegion` on ``ctx`` is
        consumed indirectly: the agent's ``GOAL_PROMPT`` and Case C
        tool description were already rendered from
        :data:`~vacantes.domain.region.COSTA_RICA_LATAM` at import
        time; parameterising them per-call is.

        ``company.hooks`` is threaded through to
        :func:`extract_jobs` so the deterministic pre-extract stages
        (CSS injection, expand rounds, walker override, filter-applied
        prompt clause) fire during the run. Corpus companies default to
        an inert :class:`~vacantes.domain.company.RuntimeHooks`
        (pydantic default), which is a byte-identical no-op relative to
        the pre-hooks code path.

        When ``company.pre_filter_urls`` is non-empty, dispatches
        to :meth:`_extract_prefiltered` — an agent-less multi-state
        union path that skips the LLM entirely and never imports
        :func:`~vacantes.extraction.dom.agent.runner.build_agent`. The
        dispatch happens *before* any call into :func:`extract_jobs`,
        so ``OPENAI_API_KEY`` is not consulted on the prefiltered path
        (structural, not conditional — closes C18 in
        ``blockers/INTEGRATION_BLOCKERS.md``). Corpus companies
        default to the empty tuple, so the single-state branch is
        byte-identical for every non-declaring entry.
        """
        if company.pre_filter_urls:
            return await self._extract_prefiltered(company, ctx)
        return await extract_jobs(
            company_name=company.name,
            job_board_url=company.job_board_url,
            sample_job_url=company.sample_job_url,
            path_prefix=company.link_rule.path_prefix,
            min_depth=company.link_rule.min_depth,
            suppress_selector=company.link_rule.suppress_ancestor_selector,
            paginate=company.paginate,
            expected_jobs=company.expected_jobs,
            hooks=company.hooks,
            model=ctx.model,
            headless=ctx.headless,
            max_steps=ctx.max_steps,
        )

    async def _extract_prefiltered(
        self, company: Company, ctx: RunContext
    ) -> dict[str, Any]:
        """Agent-less multi-state union path.

        Navigates a fresh :class:`BrowserSession` across every URL in
        ``company.pre_filter_urls`` in declaration order, running the
        deterministic matcher (or, when ``company.paginate=True``, the
        pagination walker) at each state via
        :func:`~vacantes.extraction.dom.collector.collect_job_links`
        and unioning the URL sets across states. No
        :class:`~browser_use.agent.service.Agent` is constructed, no
        ``GOAL_PROMPT`` is rendered, and
        :func:`~vacantes.extraction.dom.agent.runner.build_agent` is never
        imported on this code path — ``OPENAI_API_KEY`` is not
        consulted at any point (structural, not conditional). This is
        what makes the C18 closure structural: a Plan A entry with a
        Cloudflare-hostile board that refuses the LLM run still yields
        deterministic, region-correct extraction with the key
        absent from the environment.

        The per-state settle loop
        (``asyncio.sleep(RENDER_WAIT_SEC)`` followed by
        ``RENDER_SCROLL_COUNT`` iterations of ``window.scrollBy`` +
        ``asyncio.sleep(1)``) is byte-identical to the loop in
        ``scripts/capture_snapshot.py`` ( promoted both
        constants from capture-local literals to
        :mod:`vacantes.settings` for exactly this parity). Each
        state's DOM is therefore the same shape at matcher time
        on-disk as it is live, closing the harness-vs-runtime drift
        class before it opens.

        hooks (``pre_extract_css``, ``expand_selector``,
        ``next_control_selector``) run once *per state* — a fresh hard
        navigation between states resets the DOM, so the
        CSS-persistence limitation never applies here (re-injection
        happens naturally on each state). ``filter_already_applied``
        is prompt-side only and this path renders no prompt, so the
        flag is unread — this is why
        :class:`~vacantes.domain.company.Company` does not
        reject the combination.

        On any per-state exception (navigate / get_current_page /
        scroll / matcher-call), the ``union`` set is cleared, the
        exception's ``str(e)`` is captured into
        ``metadata.error``, and the run returns a report with
        ``jobs=[]``. ``states_visited`` still reflects the *declared*
        URL count (``len(company.pre_filter_urls)``), not the
        successfully-completed count, so the metadata line answers
        "how many states did the plan visit?" not "how many states
        succeeded?". The ``finally`` block always stops the session,
        even on early-navigation failure. A zero-anchor state is
        **not** an error — it unions the empty set and the run
        proceeds to the next state.
        """
        # Origin + base_path derivation mirrors build_controller (the
        # sole other site that derives these from a Company). Kept
        # inline rather than reaching through build_controller because
        # build_controller also registers browser-use Controller
        # actions the agent-less path never uses — importing it here
        # would drag the browser-use tool-registration surface into
        # this LLM-free path for no functional gain.
        parsed = urlparse(company.job_board_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        base_path = (
            company.link_rule.path_prefix
            if company.link_rule.path_prefix is not None
            else derive_path_prefix(company.sample_job_url)
        )

        # launch invariant: every launch site (probe, capture,
        # ground truth, agent, and now the agent-less prefiltered
        # path) agrees on the plausible UA + keychain-suppression
        # flags. Constructed *outside* the try block so the
        # ``finally: await browser_session.stop()`` is unconditional
        # even if session.start() itself raises.
        user_agent = await plausible_headless_ua()
        browser_profile = BrowserProfile(
            headless=ctx.headless,
            user_agent=user_agent,
            args=["--password-store=basic", "--use-mock-keychain"],
        )
        browser_session = BrowserSession(browser_profile=browser_profile)

        start_time = time.time()
        union: set[str] = set()
        error_str: str | None = None

        # Per-board settle, defaulting to the shared constants. A board
        # whose listing hydrates slower than the global would otherwise
        # have the matcher run against an empty document and return a
        # confident zero; ``hooks.render_wait_sec`` raises the floor for
        # that one board without moving it for the other 106. The
        # schema guarantees these are only set alongside
        # ``pre_filter_urls``, i.e. only on this code path, so capture
        # and runtime still observe the same DOM.
        wait_s = (
            company.hooks.render_wait_sec
            if company.hooks.render_wait_sec is not None
            else RENDER_WAIT_SEC
        )
        scroll_n = (
            company.hooks.render_scroll_count
            if company.hooks.render_scroll_count is not None
            else RENDER_SCROLL_COUNT
        )

        try:
            await browser_session.start()
            for url in company.pre_filter_urls:
                await browser_session.navigate_to(url)
                await asyncio.sleep(wait_s)
                page = await browser_session.get_current_page()
                if page is None:
                    # Same defensive shape as collect_job_links' own
                    # get_current_page guard (collector.py) — raise so
                    # the except path clears the union and reports the
                    # failure honestly rather than silently skipping.
                    raise RuntimeError(
                        f"BrowserSession returned no page after navigating to {url}"
                    )
                for _ in range(scroll_n):
                    await page.evaluate(
                        "() => { window.scrollBy(0, window.innerHeight); }"
                    )
                    await asyncio.sleep(1)
                state_urls = await collect_job_links(
                    browser_session,
                    base_path,
                    origin,
                    min_depth=company.link_rule.min_depth,
                    paginate=company.paginate,
                    hooks=company.hooks,
                    suppress_selector=company.link_rule.suppress_ancestor_selector,
                )
                union.update(state_urls)
        except Exception as e:
            logger.exception("Prefiltered extraction failed for %s", company.name)
            error_str = str(e)
            union.clear()
        finally:
            await browser_session.stop()

        elapsed = time.time() - start_time
        return build_report(
            strategy=DomStrategy.name,
            company_name=company.name,
            company_url=company.job_board_url,
            jobs=sorted(union),
            elapsed=elapsed,
            model=None,
            agent_steps=None,
            agent_completed=None,
            agent_had_errors=None,
            error=error_str,
            expected_jobs=company.expected_jobs,
            states_visited=len(company.pre_filter_urls),
        )


async def extract_jobs(
    company_name: str,
    job_board_url: str,
    sample_job_url: str,
    *,
    path_prefix: str | None = None,
    min_depth: int = 1,
    suppress_selector: str | None = None,
    paginate: bool = False,
    expected_jobs: int | None = None,
    hooks: RuntimeHooks | None = None,
    model: str = DEFAULT_MODEL,
    headless: bool = True,
    max_steps: int = DEFAULT_MAX_STEPS,
) -> dict[str, Any]:
    """Extract job listings from a career page using the browser-use agent.

    Kept as a free function for backwards compatibility with scripts
    and internal callers that predate the strategy port;
    :meth:`DomStrategy.extract` is a thin wrapper.

    Args:
        company_name: Name of the company.
        job_board_url: URL of the career page (the job listing page).
        sample_job_url: Example URL of an individual job posting, used
            to determine which links on the job board are valid jobs
            when ``path_prefix`` is not supplied.
        path_prefix: Optional explicit override for the same-origin path
            prefix that job links must start with. Bypasses derivation
            from ``sample_job_url``. See
            ``extraction.dom.agent.controller.build_controller`` for details.
        min_depth: Minimum path-tail depth in the id-in-path branch of
            the matcher (mirrors ``LinkRule.min_depth``). Default of
            ``1`` is byte-identical to the legacy matcher. See
            ``extraction.dom.agent.controller.build_controller`` for details.
        suppress_selector: container-suppression selector
            (mirrors ``LinkRule.suppress_ancestor_selector``). ``None``
            is byte-identical to the legacy matcher. See
            ``extraction.dom.agent.controller.build_controller`` for details.
        paginate: opt-in (mirrors ``Company.paginate``). When
            ``True`` the collector delegates to
            :func:`~vacantes.extraction.dom.collector.walk_and_collect`,
            which unions matcher results across paginated DOM states via
            a driver-side click. Default ``False`` is byte-identical to
            the single-shot behaviour.
        expected_jobs: human-counted target (mirrors
            ``Company.expected_jobs``). Threaded straight through to
            :func:`~vacantes.extraction.base.build_report` so the
            emitted ``metadata.verdict`` reflects the run's
            found-vs-expected classification. ``None`` (the default)
            preserves the pre-verdict legacy signature — scripts and
            internal callers that construct an ``extract_jobs`` call
            without a ``Company`` instance stay working, and their
            reports emit ``verdict="unverified"``.
        hooks: :class:`~vacantes.domain.company.RuntimeHooks`
            executed between agent handoff and matcher invocation.
            Forwarded verbatim to :func:`build_controller` (the single
            normalisation site), where ``None`` is converted to an
            inert instance. ``filter_already_applied`` is read here
            via ``(hooks or RuntimeHooks()).filter_already_applied``
            and threaded to :func:`build_agent` — a read-only peek at
            the flag, NOT a second normalisation site (no local
            variable holds a normalised hooks object outside the
            argument to :func:`build_controller`). Default ``None``
            preserves the pre-hooks legacy signature.
        model: OpenAI model identifier.
        headless: Run browser in headless mode.
        max_steps: Maximum agent steps before stopping.

    Returns:
        Dict with company info, extraction result, and metadata (shape
        authored by
        :func:`~vacantes.extraction.base.build_report`).
    """
    # Deferred imports: ``extraction.dom.agent.controller`` transitively loads
    # ``extraction/__init__.py`` (via ``from ...extraction.dom.collector
    # import collect_job_links``), which loads this strategy module.
    # Importing these at module top would create an order-sensitive
    # cycle that is dormant along the CLI's import path but active for
    # any caller that imports ``extraction.dom.agent.controller`` first (e.g. the
    # ``extractor_ground_truth.py`` diagnostic used by the
    # integrate-company workflow). Function-local imports resolve after
    # both modules have fully initialised.
    from vacantes.extraction.dom.agent.controller import build_controller
    from vacantes.extraction.dom.agent.runner import build_agent

    controller = build_controller(
        job_board_url,
        sample_job_url,
        path_prefix=path_prefix,
        min_depth=min_depth,
        suppress_selector=suppress_selector,
        paginate=paginate,
        hooks=hooks,
    )
    # Read-only peek at filter_already_applied — NOT a second
    # normalisation site. build_controller (above) owns the sole
    # None -> RuntimeHooks() conversion for the internal plumbing;
    # this expression short-lives an inert instance only to safely
    # access the flag without introducing a local hooks variable.
    filter_already_applied = (hooks or RuntimeHooks()).filter_already_applied
    agent, browser_session = await build_agent(
        job_board_url=job_board_url,
        controller=controller,
        model=model,
        headless=headless,
        filter_already_applied=filter_already_applied,
    )

    start_time = time.time()
    history: AgentHistoryList | None = None

    try:
        history = await agent.run(max_steps=max_steps)
        elapsed = time.time() - start_time

        result = _parse_result(history)
        return _build_output(
            company_name=company_name,
            company_url=job_board_url,
            model=model,
            result=result,
            history=history,
            elapsed=elapsed,
            error=None,
            expected_jobs=expected_jobs,
        )
    except Exception as e:
        elapsed = time.time() - start_time
        logger.exception("Agent extraction failed for %s", company_name)
        return _build_output(
            company_name=company_name,
            company_url=job_board_url,
            model=model,
            result=None,
            history=history,
            elapsed=elapsed,
            error=str(e),
            expected_jobs=expected_jobs,
        )
    finally:
        await browser_session.stop()


def _parse_result(history: AgentHistoryList) -> ExtractionResult | None:
    """Parse structured extraction result from agent history."""
    raw = history.final_result()
    if not raw:
        return None

    # Try direct parse
    try:
        return ExtractionResult.model_validate_json(raw)
    except Exception:
        pass

    # Try parsing as JSON, handling double-encoded values
    try:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            text = text.rsplit("```", 1)[0]
        data = json.loads(text)
        # Handle double-encoded jobs field
        if isinstance(data.get("jobs"), str):
            data["jobs"] = json.loads(data["jobs"])
        return ExtractionResult.model_validate(data)
    except Exception:
        logger.warning("Could not parse agent output: %s", raw[:200])
        return None


def _build_output(
    *,
    company_name: str,
    company_url: str,
    model: str,
    result: ExtractionResult | None,
    history: AgentHistoryList | None,
    elapsed: float,
    error: str | None,
    expected_jobs: int | None,
) -> dict[str, Any]:
    """Build the final output dict from agent history + parsed result.

    Delegates to :func:`~vacantes.extraction.base.build_report` for
    the actual report shape. The DOM strategy always emits concrete
    integer / boolean agent fields — ``0`` / ``False`` / ``True`` when
    ``history`` is ``None`` (agent failed before completing a step) —
    to stay byte-compatible with the legacy report; API strategies
    emit ``None`` for the same fields.

    ``expected_jobs`` is passed through unchanged from
    ``Company.expected_jobs`` (or ``None`` for legacy free-function
    callers). Verdict classification is centralised in
    :func:`~vacantes.extraction.base.compute_verdict` — this
    helper never inspects or transforms the value.
    """
    jobs_data: list[str] = result.model_dump()["jobs"] if result else []
    return build_report(
        strategy=DomStrategy.name,
        company_name=company_name,
        company_url=company_url,
        jobs=jobs_data,
        elapsed=elapsed,
        model=model,
        agent_steps=history.number_of_steps() if history else 0,
        agent_completed=history.is_done() if history else False,
        agent_had_errors=history.has_errors() if history else True,
        error=error,
        expected_jobs=expected_jobs,
    )
