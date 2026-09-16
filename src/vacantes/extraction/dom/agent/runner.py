"""Runtime browser-use ``Agent`` construction.

``build_agent`` wires the LLM (LiteLLM/OpenAI), the browser profile with
the macOS/Linux keychain-suppression flags, the browser session, and the
task prompt into a fully-configured ``Agent`` ready to ``.run()``.

The agent-tuning knobs (``use_vision=False``, ``max_actions_per_step=3``,
``max_failures=3``, ``use_judge=False`` with its rationale comment) are
kept **verbatim** from the pre- ``agent.py`` — will introduce
per-company overrides but this phase preserves runtime behaviour to the
byte.

The returned tuple hands the caller both the ``Agent`` and the
``BrowserSession`` so the caller can ensure the session is stopped in a
``finally`` block regardless of whether ``agent.run()`` raises.
"""

from __future__ import annotations

from browser_use.agent.service import Agent
from browser_use.browser.profile import BrowserProfile
from browser_use.browser.session import BrowserSession
from browser_use.controller import Controller
from browser_use.llm.litellm.chat import ChatLiteLLM

from vacantes.domain.region import COSTA_RICA_LATAM
from vacantes.extraction.dom.agent.prompt import GOAL_PROMPT, build_goal_prompt
from vacantes.settings import plausible_headless_ua


async def build_agent(
    *,
    job_board_url: str,
    controller: Controller,
    model: str,
    headless: bool,
    filter_already_applied: bool = False,
) -> tuple[Agent, BrowserSession]:
    """Assemble the runtime ``Agent`` + ``BrowserSession`` for one run.

    ``async`` because the browser-environment layer resolves the
    plausible User-Agent via a headless Chromium probe before wiring
    the ``BrowserProfile``. ``BrowserProfile.user_agent`` maps to
    Chromium's ``--user-agent=`` launch flag, so the UA must be known
    *before* the browser launches — the resolver runs once per process
    and caches its result, so subsequent ``build_agent`` calls do not
    re-launch. See ``vacantes.settings.plausible_headless_ua``
    for the C14 rationale (Dev.Pro WAF greps for ``HeadlessChrome``).

    The plausible UA is applied unconditionally, including on headed
    runs: the transform is a byte-identical no-op when the input UA
    does not carry the ``HeadlessChrome`` token, so headed runs remain
    byte-compatible with the legacy behaviour while the invariant
    "every launch site presents the same UA" stays unconditional.

    Args:
        job_board_url: Career page URL — used to build the task prompt.
        controller: Pre-built controller carrying the site-specific
            ``extract_job_links`` / ``report_no_matching_location_filter``
            tools (see ``extraction.dom.agent.controller.build_controller``).
        model: OpenAI model identifier (e.g. ``"gpt-4.1-mini"``).
        headless: Whether to launch Chromium headless.
        filter_already_applied: :attr:`RuntimeHooks.filter_already_applied`.
            When ``False`` (default) the task prompt is the module-level
            :data:`GOAL_PROMPT` — byte-identical to the pre-hooks shape.
            When ``True`` the task prompt is re-rendered via
            :func:`~vacantes.extraction.dom.agent.prompt.build_goal_prompt`
            with ``filter_already_applied=True``, inserting the "filter
            already applied" NOTE after the intro so the agent skips
            filter-control interaction on boards whose ``job_board_url``
            already encodes the region in its query string.
    """
    llm = ChatLiteLLM(model=f"openai/{model}", temperature=1)

    # all launch sites must agree — a board is validated (probe,
    # capture, ground truth) and run (this Agent) under one browser
    # environment.
    user_agent = await plausible_headless_ua()

    # --password-store=basic / --use-mock-keychain stop Chromium from reaching
    # into the OS keychain on launch, which otherwise pops a login/"root"
    # password prompt on macOS (and a libsecret prompt on Linux).
    browser_profile = BrowserProfile(
        headless=headless,
        user_agent=user_agent,
        args=["--password-store=basic", "--use-mock-keychain"],
    )
    browser_session = BrowserSession(browser_profile=browser_profile)

    goal_prompt: str = (
        build_goal_prompt(COSTA_RICA_LATAM, filter_already_applied=True)
        if filter_already_applied
        else GOAL_PROMPT
    )
    task = f"Go to {job_board_url}\n\n{goal_prompt}"

    agent: Agent = Agent(
        task=task,
        llm=llm,
        browser_session=browser_session,
        controller=controller,
        use_vision=False,
        max_actions_per_step=3,
        max_failures=3,
        # The built-in LLM judge costs an extra call per run and grades the
        # agent trace against the literal task text (with no ground-truth job
        # count), so it FAILs correct runs. We measure success ourselves via
        # jobs-found + agent_completed/agent_had_errors instead.
        use_judge=False,
    )

    return agent, browser_session
