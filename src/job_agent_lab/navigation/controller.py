"""Browser-use ``Controller`` factory for the job-extraction agent.

``build_controller`` registers two custom tools on a fresh
``browser_use.Controller``:

- ``extract_job_links`` — runs ``EXTRACT_JOB_LINKS_JS`` against the
  current page via ``extraction.dom.collector.collect_job_links`` and
  returns the ``{"jobs": [...]}`` JSON payload as an ``ActionResult``
  with ``is_done=True``. The tool takes no arguments; the ``base_path``
  and ``origin`` needed by the matcher are closed over from the
  arguments passed to ``build_controller``.

- ``report_no_matching_location_filter`` — the ``GOAL_PROMPT`` Case C
  escape hatch. Also finishes the run, with an empty jobs list.

The ``base_path`` for the matcher is either the caller-supplied
``path_prefix`` override or the default derived from ``sample_job_url``
via ``extraction.dom.rules.derive_path_prefix``. The origin is derived
from ``job_board_url``.

Note: this module deliberately does NOT use ``from __future__ import
annotations``. Under PEP 563 the ``browser_session: BrowserSession``
parameter of the registered ``extract_job_links`` action would be a
string at registration time, and browser-use's action registry
(``_normalize_action_function_signature``) compares that annotation
against the actual ``BrowserSession`` class to authorise the special-
parameter injection. Deferred annotations turn that check into a
``str == class`` mismatch and the registration raises ``ValueError:
... conflicts with special argument injected by tools``.
"""

import json
from urllib.parse import urlparse

from browser_use.agent.views import ActionResult
from browser_use.browser.session import BrowserSession
from browser_use.controller import Controller

from job_agent_lab.domain.company import RuntimeHooks
from job_agent_lab.domain.region import COSTA_RICA_LATAM, TargetRegion
from job_agent_lab.extraction.dom.collector import collect_job_links
from job_agent_lab.extraction.dom.rules import derive_path_prefix


def build_no_match_description(region: TargetRegion) -> str:
    """Render the Case C tool description for the given :class:`TargetRegion`.

    The description is a *second* rendering of the same
    ``filter_tokens`` that :mod:`job_agent_lab.navigation.prompt`
    renders into ``GOAL_PROMPT`` — same tokens, comma-form and unquoted
    (browser-use surfaces tool descriptions to the model as plain
    prose, not as quoted string enumerations). ``build_controller``
    passes the result straight to the action decorator; a golden test
    in ``tests/unit/test_prompt_render.py`` pins the
    :data:`COSTA_RICA_LATAM` render byte-identical to the pre-SYS-4
    literal.
    """
    return (
        "Report that this job board has a location or region filter, but "
        f"the filter offers none of {region.format_unquoted_options()} "
        "among its options. Call this INSTEAD of extract_job_links in that "
        "scenario (GOAL_PROMPT Case C). Requires no arguments. It finishes "
        "the run with an empty job list, signalling that this company has "
        "no listings applicable to the target region."
    )


def build_controller(
    job_board_url: str,
    sample_job_url: str,
    *,
    path_prefix: str | None = None,
    min_depth: int = 1,
    suppress_selector: str | None = None,
    paginate: bool = False,
    region: TargetRegion = COSTA_RICA_LATAM,
    hooks: RuntimeHooks | None = None,
) -> Controller:
    """Build a Controller with a custom extract_job_links tool.

    Args:
        job_board_url: The career page URL (the listing page).
        sample_job_url: An example URL of an individual job posting, used
            to derive the path prefix that identifies valid job links when
            ``path_prefix`` is not supplied.
        path_prefix: Optional explicit override for the same-origin path
            prefix that job links must start with. When provided, this
            string is used verbatim and ``sample_job_url`` is not consulted
            for prefix derivation. Use it for boards whose URL shape does
            not fit the "strip the last path segment" heuristic (e.g.
            Simplicant's ``/jobs/<id-slug>/detail``).
        min_depth: Minimum path-tail depth in the id-in-path branch of
            the matcher (mirrors ``LinkRule.min_depth``). The default of
            ``1`` is byte-identical to the pre-SYS-3 matcher; raise it
            when the board's chrome links share the prefix at shallow
            depths and real postings live deeper (C9: Databricks,
            Avionyx/iCIMS).
        suppress_selector: SYS-14 container-suppression selector
            (mirrors ``LinkRule.suppress_ancestor_selector``). Anchors
            inside a matching ancestor are dropped by the matcher. Use
            it when a board renders a section whose anchors are
            URL-indistinguishable from the real postings (C16: Ulteig's
            UKG "Featured opportunities"). ``None`` (the default) is
            byte-identical to the pre-SYS-14 matcher.
        paginate: SYS-5 opt-in for boards that partition their listing
            across multiple DOM states (Techwarely, BCG). When ``True``
            the registered ``extract_job_links`` tool delegates to
            :func:`~job_agent_lab.extraction.dom.collector.walk_and_collect`,
            which advances state via a driver-side click on the
            ``[data-jal-next]`` marker stamped by the discovery JS. The
            default of ``False`` is byte-identical to the pre-SYS-5
            single-shot code path.
        region: Target :class:`TargetRegion` whose ``filter_tokens`` are
            rendered into the Case C tool description. Defaults to
            :data:`COSTA_RICA_LATAM` so existing callers (the CLI, the
            ground-truth script) keep their pre-SYS-4 signatures.
        hooks: SYS-12 :class:`RuntimeHooks` executed between agent
            handoff and matcher invocation. This is the **single
            normalisation site** for the plumbing chain — ``None`` is
            converted to an inert :class:`RuntimeHooks` here and the
            normalised value is forwarded verbatim to
            :func:`~job_agent_lab.extraction.dom.collector.collect_job_links`.
            Downstream consumers gate on :attr:`RuntimeHooks.is_inert`
            (never ``is None``) so the pre-SYS-12 code path is exactly
            recovered when the caller passes ``None`` or an inert
            instance. ``filter_already_applied`` is read by the
            prompt-rendering layer only (see
            :func:`~job_agent_lab.navigation.runner.build_agent`) and is
            not consulted inside the tool wrapper.
    """
    controller: Controller = Controller()

    # Single-site normalisation: every downstream consumer sees a
    # concrete RuntimeHooks and can gate on is_inert. Callers that
    # already hold a Company instance pass company.hooks (guaranteed
    # non-None by the pydantic default); the None branch exists for
    # the free-function extract_jobs facade and its legacy callers.
    hooks_normalised: RuntimeHooks = hooks if hooks is not None else RuntimeHooks()

    parsed = urlparse(job_board_url)
    origin_str = f"{parsed.scheme}://{parsed.netloc}"
    base_path = (
        path_prefix if path_prefix is not None else derive_path_prefix(sample_job_url)
    )

    @controller.registry.action(
        "Extract all job listing links from the current page. Call this "
        "once you have navigated to the job listings page, applied any "
        "filters, and scrolled to reveal all listings. The tool already "
        "knows how to identify valid job links. No arguments needed."
    )
    async def extract_job_links(
        browser_session: BrowserSession,
    ) -> ActionResult:
        urls = await collect_job_links(
            browser_session,
            base_path,
            origin_str,
            min_depth=min_depth,
            suppress_selector=suppress_selector,
            paginate=paginate,
            hooks=hooks_normalised,
        )
        result = json.dumps({"jobs": urls})
        return ActionResult(
            is_done=True,
            extracted_content=result,
        )

    @controller.registry.action(build_no_match_description(region))
    async def report_no_matching_location_filter() -> ActionResult:
        result = json.dumps({"jobs": []})
        return ActionResult(
            is_done=True,
            extracted_content=result,
        )

    return controller
