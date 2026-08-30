"""Runtime settings and defaults for the job-agent lab.

Constants that describe *how* a run behaves (model, step cap, output
directory) rather than *what* it runs against (companies live in
``catalog``) or *how it decides* (prompt lives in ``navigation.prompt``).

Kept as plain module-level constants where possible; a settings class is
unnecessary until an environment variable earns one. The SYS-10 browser-
environment layer breaks this rule deliberately: the plausible-UA
resolver is a coroutine backed by a module-level cache because obtaining
the launch-time User-Agent requires an actual headless Chromium probe
(the token that identifies a headless launch is composed inside Chromium
at launch and is not available as a static string). Everything else in
this module remains a plain constant.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_MODEL: str = "gpt-4.1-mini"
DEFAULT_MAX_STEPS: int = 20

# Default to an "output/" directory relative to the current working
# directory. The CLI's -o flag overrides this per invocation.
OUTPUT_DIR: Path = Path("output")

# ---------------------------------------------------------------------------
# Shared render-settle defaults (SYS-13)
# ---------------------------------------------------------------------------
#
# Two consumers rely on the same "navigate → wait → scroll" recipe to
# bring a lazy career-page listing into a stable, extractable state:
#
# 1. ``scripts/capture_snapshot.py`` — the capture-side settle before
#    baking ``page.html`` (its ``--wait`` / ``--scroll`` argparse flags
#    default to these values).
# 2. ``DomStrategy._extract_prefiltered`` (SYS-13) — the runtime
#    per-state settle in the agent-less multi-state path, executed
#    once per URL in ``Company.pre_filter_urls`` before invoking the
#    matcher. Byte-matching the capture recipe here is what keeps the
#    runtime and the frozen fixtures observing the same DOM.
#
# Kept as module-level constants (not a settings class) matching every
# other value in this file; the capture flags override them per
# invocation for one-off tuning, and the runtime consumer reads them
# directly (no override — the runtime path has no operator surface for
# this and never has needed one).
RENDER_WAIT_SEC: int = 8
RENDER_SCROLL_COUNT: int = 3

# ---------------------------------------------------------------------------
# SYS-10: browser-environment layer — plausible User-Agent
# ---------------------------------------------------------------------------
#
# Dev.Pro's WAF returns HTTP 403 to any browser whose User-Agent string
# contains the substring ``HeadlessChrome`` (C14 in
# ``blockers/INTEGRATION_BLOCKERS_R2.md``). The three-way isolation
# experiment recorded there pins the mechanism precisely: the WAF greps
# for the literal ``HeadlessChrome`` token in the UA header — no other
# bot-detection surface (``navigator.webdriver``, canvas fingerprint,
# TLS fingerprint, request rate) is checked. Stripping the token from
# the UA — replacing ``HeadlessChrome/<v>`` with ``Chrome/<v>`` — closes
# C14 at every launch site (runtime agent, probe, capture, ground-truth
# diagnostic).
#
# The two helpers below are the single source of truth for the derived
# UA: ``derive_plausible_ua`` is the pure string transform (unit-tested
# in isolation) and ``plausible_headless_ua`` is the memoized async
# resolver that all five launch sites consume before invoking their
# browser-launch surface.


# Module-level cache. Populated on the first ``plausible_headless_ua()``
# call; every subsequent call inside the same process returns the same
# ``str`` instance. Kept as a bare ``str | None`` (not wrapped in a
# ``functools.cache``-style decorator) so the resolver stays plainly
# async — ``functools.cache`` does not support coroutines.
_PLAUSIBLE_UA: str | None = None


def derive_plausible_ua(default_ua: str) -> str:
    """Strip the ``HeadlessChrome/<v>`` token from a Chromium UA string.

    Pure string transform: replaces the product token
    ``HeadlessChrome/`` with ``Chrome/``. The version segment following
    the slash (e.g. ``149.0.7827.55``) is preserved verbatim, so the UA
    stays consistent with the Chromium binary the runtime actually
    launches — no version pinning, no drift as Playwright updates its
    bundled Chromium.

    A headed launch's default UA does not contain the ``HeadlessChrome``
    token in the first place, so passing a headed UA through this
    function is a byte-identical no-op. That property is deliberate:
    every launch site can call the resolver unconditionally, keeping
    the environment invariant identical across headed and headless
    runs.

    Args:
        default_ua: The UA string Chromium composes at launch. Typically
            obtained by reading ``navigator.userAgent`` from a throwaway
            page in a headless Chromium process.

    Returns:
        The UA with the ``HeadlessChrome/`` token replaced by
        ``Chrome/``. Byte-identical to the input when the token is
        absent.
    """
    # The ``HeadlessChrome/`` token appears exactly once in a Chromium
    # UA and only when the launch is headless — a single ``str.replace``
    # is sufficient (and byte-identical to a regex substitution for
    # this fixed literal).
    return default_ua.replace("HeadlessChrome/", "Chrome/")


async def plausible_headless_ua() -> str:
    """Resolve the plausible UA once per process; return the cached value thereafter.

    Launches the shared Playwright Chromium binary **headless** (with
    the standing keychain-suppression args every runtime launch site
    uses), reads ``navigator.userAgent`` from a throwaway page, closes
    the browser, and returns
    :func:`derive_plausible_ua` applied to that string.

    The result is cached at module level: the second and every
    subsequent call inside the same process returns the identical
    ``str`` instance without launching Chromium again (memoization is
    observable via ``is``). One probe launch per process (~0.5 s)
    amortises across every extraction the process runs.

    ``BrowserProfile.user_agent`` in the installed browser-use maps to
    Chromium's ``--user-agent=`` launch flag, so this helper must run
    *before* the launch that consumes its result — every call site
    ``await``s the helper and passes the returned string to its
    launch-surface UA argument.

    Returns:
        The plausible UA string — a Chromium UA with the
        ``HeadlessChrome/<v>`` token replaced by ``Chrome/<v>``.
    """
    global _PLAUSIBLE_UA
    if _PLAUSIBLE_UA is not None:
        return _PLAUSIBLE_UA

    # Local import: ``playwright`` is a heavy transitive of
    # ``browser-use`` and importing it at module top would make
    # ``job_agent_lab.settings`` (a leaf module imported by the schema,
    # the catalog, the CLI, and the reporting layer) unnecessarily
    # expensive to load. The resolver is only ever called from the
    # launch sites, which have already paid the Playwright import cost.
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--password-store=basic", "--use-mock-keychain"],
        )
        try:
            page = await browser.new_page()
            default_ua: str = await page.evaluate("() => navigator.userAgent")
        finally:
            await browser.close()

    _PLAUSIBLE_UA = derive_plausible_ua(default_ua)
    return _PLAUSIBLE_UA
