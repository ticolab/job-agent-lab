#!/usr/bin/env python3
"""Run the deterministic extractor against a freshly-rendered page.

This is "Appendix B" of the integrate-company workflow: it runs the actual
``vacantes.extraction.dom.agent.controller.build_controller`` extractor (the same
code vacantes uses end-to-end) over a real rendered DOM, with no LLM in the loop.
Use it as the fast, free iteration loop while finding a working
``(job_board_url, sample_job_url)`` combination.

Run from the vacantes repo so ``vacantes`` is importable. See
``--help`` for the full argument list. For lazy-loaded sites, raise ``--wait``
and/or pass ``--scroll N``. For boards whose full listing is partitioned
across multiple DOM states advanced by an in-page "next" control (SYS-5:
Techwarely, BCG), pass ``--paginate`` to exercise the same walker code path
``Company.paginate=True`` triggers at runtime.

For boards that render a section whose anchors are indistinguishable
from real postings at the URL layer — same origin, same path prefix,
same depth, same shape — pass ``--suppress-ancestor-selector CSS``
(SYS-14, mirrors ``Company.link_rule.suppress_ancestor_selector``). An
anchor is dropped when ``anchor.closest(CSS)`` is non-null. The
motivating board is Ulteig (C16), whose UKG UltiPro board renders a
personalized "Featured opportunities" block beside the real filtered
results; ``[data-automation="featured-opportunities"]`` excludes it.

For boards that need SYS-12 deterministic page preparation before the
matcher runs, three flags surface subsets of ``Company.hooks`` for
free-loop iteration: ``--pre-extract-css`` (inject a stylesheet before
the matcher — motivating board: SentinelOne's Tailwind ``md:hidden``
gate), ``--expand-selector`` (drive the bounded expansion loop that
mounts collapsed listing sections — motivating board: Deel's accordion
headers), and ``--next-control-selector`` (bypass discovery signals 1–6
with an explicit CSS selector for the walker's next-page control —
motivating board: Progress's aria-hidden Next button). The
``--next-control-selector`` flag requires ``--paginate``, mirroring
the ``_validate_next_control_selector_requires_paginate`` validator on
``Company``. The fourth ``RuntimeHooks`` field, ``filter_already_applied``,
is deliberately absent: it is a prompt-only hook consumed by
``build_goal_prompt`` and has no surface in a GT run that never
constructs the LLM agent.

No ``OPENAI_API_KEY`` is required.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from browser_use.browser.profile import BrowserProfile
from browser_use.browser.session import BrowserSession

from vacantes.domain.company import RuntimeHooks
from vacantes.extraction.dom.agent.controller import build_controller
from vacantes.settings import plausible_headless_ua


async def _run(
    job_board_url: str,
    sample_job_url: str,
    wait: int,
    scroll: int,
    path_prefix: str | None,
    min_depth: int,
    suppress_selector: str | None,
    paginate: bool,
    hooks: RuntimeHooks | None,
) -> int:
    controller = build_controller(
        job_board_url,
        sample_job_url,
        path_prefix=path_prefix,
        min_depth=min_depth,
        suppress_selector=suppress_selector,
        paginate=paginate,
        hooks=hooks,
    )
    action = controller.registry.registry.actions["extract_job_links"]
    # SYS-10: all launch sites must agree — a board is validated and run
    # under one browser environment. The plausible UA
    # (``HeadlessChrome/<v>`` → ``Chrome/<v>``) closes the C14 WAF-403
    # class documented in ``blockers/INTEGRATION_BLOCKERS_R2.md``.
    profile = BrowserProfile(
        headless=True,
        user_agent=await plausible_headless_ua(),
        args=["--password-store=basic", "--use-mock-keychain"],
    )
    session = BrowserSession(browser_profile=profile)
    await session.start()
    try:
        await session.navigate_to(job_board_url)
        await asyncio.sleep(wait)
        if scroll:
            page = await session.get_current_page()
            if page is None:
                raise RuntimeError(
                    "No active page in browser session after navigation."
                )
            for _ in range(scroll):
                await page.evaluate("() => { window.scrollBy(0, window.innerHeight); }")
                await asyncio.sleep(1)
        result = await action.function(browser_session=session)
        jobs = json.loads(result.extracted_content)["jobs"]
        print(f"EXTRACTOR RETURNED: {len(jobs)}")
        for url in jobs:
            print(f"  {url}")
        return len(jobs)
    finally:
        await session.stop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deterministic job-extractor ground-truth diagnostic.",
    )
    parser.add_argument(
        "--job-board-url",
        required=True,
        help="The job board URL the extractor should navigate to.",
    )
    parser.add_argument(
        "--sample-job-url",
        required=True,
        help="A real job URL used to derive the path prefix and origin.",
    )
    parser.add_argument(
        "--path-prefix",
        default=None,
        help=(
            "Optional explicit prefix override that bypasses derivation from "
            "--sample-job-url. Mirrors the Company.path_prefix field in "
            "config.py. Use for boards whose URL shape does not fit the "
            "default 'strip the last path segment' heuristic."
        ),
    )
    parser.add_argument(
        "--min-depth",
        type=int,
        default=1,
        help=(
            "Minimum path-tail depth in the id-in-path branch of the "
            "matcher. Mirrors the Company.link_rule.min_depth field. "
            "Default of 1 is byte-identical to the pre-SYS-3 matcher; "
            "raise it (typically to 2) for boards whose chrome links "
            "share the job-link prefix at shallow depths while real "
            "postings live deeper (C9: Databricks, Avionyx/iCIMS)."
        ),
    )
    parser.add_argument(
        "--suppress-ancestor-selector",
        default=None,
        metavar="CSS",
        help=(
            "Drop anchors that sit inside a container matching this CSS "
            "selector (anchor.closest(SEL) is non-null). Mirrors the "
            "Company.link_rule.suppress_ancestor_selector field. Use it "
            "for boards that render a section whose anchors are "
            "indistinguishable from real postings at the URL layer — "
            "same origin, prefix, depth, and shape (C16: Ulteig's UKG "
            "'Featured opportunities', "
            "'[data-automation=\"featured-opportunities\"]'). The gate "
            "runs after the visibility gate and before URL bucketing, so "
            "it drops visible anchors by design. An unparseable selector "
            "is a loud error, not a silent no-op."
        ),
    )
    parser.add_argument(
        "--paginate",
        action="store_true",
        help=(
            "Enable the SYS-5 pagination walker (mirrors Company.paginate). "
            "The collector unions matcher results across paginated DOM "
            "states via a driver-side click on the discovery-JS marker. "
            "Default (flag omitted) is byte-identical to the pre-SYS-5 "
            "single-shot behaviour."
        ),
    )
    parser.add_argument(
        "--pre-extract-css",
        default=None,
        help=(
            "SYS-12: CSS text injected into a top-document <style> tag "
            "before the matcher runs (mirrors "
            "Company.hooks.pre_extract_css). Motivating board: "
            "SentinelOne's Tailwind 'md:hidden' gate — supply "
            "'.md\\:hidden{display:block!important}' to un-hide the "
            "listing region. Invalid CSS (non-whitespace text that "
            "parses to zero rules) is a loud error at runtime."
        ),
    )
    parser.add_argument(
        "--expand-selector",
        default=None,
        help=(
            "SYS-12: CSS selector for a repeatable 'expand' affordance "
            "(mirrors Company.hooks.expand_selector). When set, the "
            "collector runs bounded click-all rounds via expand_all "
            "before invoking the matcher; each round stamps every "
            "visible match with an indexed 'data-jal-expand' marker "
            "and clicks each stamp through the PageDriver seam, up to "
            "EXPAND_MAX_ROUNDS. Motivating board: Deel's "
            'button[aria-expanded="false"] accordion headers.'
        ),
    )
    parser.add_argument(
        "--next-control-selector",
        default=None,
        help=(
            "SYS-12: CSS selector for the walker's next-page control "
            "that bypasses signals 1–6 in find_next_control.js "
            "(mirrors Company.hooks.next_control_selector). Requires "
            "--paginate, mirroring the "
            "_validate_next_control_selector_requires_paginate "
            "validator on Company. Motivating board: Progress's "
            "aria-hidden Next button that the generic signals cannot "
            "match. First querySelector match wins; null match "
            "terminates the walker at state 1 without falling back "
            "to signals 1–6."
        ),
    )
    parser.add_argument(
        "--wait",
        type=int,
        default=6,
        help="Seconds to wait after navigation for JS rendering (default 6).",
    )
    parser.add_argument(
        "--scroll",
        type=int,
        default=0,
        help="Viewport-height scrolls to perform after the wait (default 0).",
    )
    args = parser.parse_args()
    # Mirror Company._validate_next_control_selector_requires_paginate:
    # a walker-discovery override only makes sense when the walker is
    # engaged. Failing at argparse time keeps the error message co-
    # located with the CLI surface rather than surfacing as a pydantic
    # ValidationError deep inside build_controller.
    if args.next_control_selector is not None and not args.paginate:
        parser.error("--next-control-selector requires --paginate")
    # Construct a RuntimeHooks only when at least one hook flag is set,
    # keeping the ``hooks=None`` code path byte-identical to the pre-
    # SYS-12 invocation for corpus boards that don't need hooks.
    if (
        args.pre_extract_css is not None
        or args.expand_selector is not None
        or args.next_control_selector is not None
    ):
        hooks: RuntimeHooks | None = RuntimeHooks(
            pre_extract_css=args.pre_extract_css,
            expand_selector=args.expand_selector,
            next_control_selector=args.next_control_selector,
        )
    else:
        hooks = None
    try:
        asyncio.run(
            _run(
                args.job_board_url,
                args.sample_job_url,
                args.wait,
                args.scroll,
                args.path_prefix,
                args.min_depth,
                args.suppress_ancestor_selector,
                args.paginate,
                hooks,
            )
        )
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
