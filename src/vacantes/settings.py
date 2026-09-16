"""Runtime settings and defaults shared by every component.

Constants that describe *how* a run behaves (model, step cap, output
directory) rather than *what* it runs against (companies live in
``catalog``) or *how it decides* (prompt lives in ``extraction.dom.agent.prompt``).

Kept as plain module-level constants where possible; a settings class is
unnecessary until an environment variable earns one. The browser-
environment layer breaks this rule deliberately: the plausible-UA
resolver is a coroutine backed by a module-level cache because obtaining
the launch-time User-Agent requires an actual headless Chromium probe
(the token that identifies a headless launch is composed inside Chromium
at launch and is not available as a static string). Everything else in
this module remains a plain constant.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:  # pragma: no cover - typing-only import
    from playwright.async_api import Browser, Playwright

logger = logging.getLogger(__name__)

DEFAULT_MODEL: str = "gpt-4.1-mini"
DEFAULT_MAX_STEPS: int = 20

# Default to an "output/" directory relative to the current working
# directory. The CLI's -o flag overrides this per invocation.
OUTPUT_DIR: Path = Path("output")

# Default SQLite database location, relative to the current working
# directory like OUTPUT_DIR. ``data/`` is gitignored: the dataset answers
# "what is open right now" and is fully regenerable by re-running a
# batch, so it is never committed and backup is a file copy.
#
# Stored as a filesystem path rather than a driver URL because the path
# is what an operator types (``sqlite3 data/vacantes.db '...'``) and what
# a backup copies. ``persistence.engine`` builds the
# ``sqlite+aiosqlite://`` URL from it, which keeps URL construction in
# exactly one place for the eventual PostgreSQL move.
DEFAULT_DATABASE_PATH: Path = Path("data") / "vacantes.db"

# The one environment variable this module reads. It exists because two
# programs must agree on where the database is — ``vacantes batch``,
# which writes it, and ``alembic``, which migrates it — and only one of
# them has a command-line flag. ``vacantes batch --database PATH`` can
# point a run anywhere, but Alembic reads this module and nothing else,
# so without a shared knob the preflight's "run alembic upgrade head"
# would migrate the default file rather than the one the operator named.
#
# A *shell* variable, deliberately not a ``.env`` key: Alembic never
# loads ``.env``, and a value that reached one program but not the other
# would recreate exactly the split this variable exists to close.
DATABASE_ENV_VAR: str = "VACANTES_DB"


def database_path_from_env(environ: Mapping[str, str] = os.environ) -> Path:
    """Resolve the database path from *environ*, falling back to the default.

    A pure function over a mapping so the resolution rule is testable
    without reloading this module or mutating the process environment.
    A set-but-blank variable is treated as unset rather than as a path
    named ``""``.
    """
    raw = environ.get(DATABASE_ENV_VAR, "").strip()
    return Path(raw) if raw else DEFAULT_DATABASE_PATH


DATABASE_PATH: Path = database_path_from_env()

# ---------------------------------------------------------------------------
# Shared render-settle defaults
# ---------------------------------------------------------------------------
#
# Two consumers rely on the same "navigate → wait → scroll" recipe to
# bring a lazy career-page listing into a stable, extractable state:
#
# 1. ``scripts/capture_snapshot.py`` — the capture-side settle before
#    baking ``page.html`` (its ``--wait`` / ``--scroll`` argparse flags
#    default to these values).
# 2. ``DomStrategy._extract_prefiltered`` — the runtime
#    per-state settle in the agent-less multi-state path, executed
#    once per URL in ``Company.pre_filter_urls`` before invoking the
#    matcher. Byte-matching the capture recipe here is what keeps the
#    runtime and the frozen fixtures observing the same DOM.
#
# Kept as module-level constants (not a settings class) matching every
# other value in this file. Both consumers resolve a three-level chain:
# an explicit ``--wait`` / ``--scroll`` capture flag, else the board's
# ``RuntimeHooks.render_wait_sec`` / ``render_scroll_count``, else these
# defaults. The per-board level was added for Edwards Lifesciences,
# whose Algolia/React-InstantSearch listing renders no job anchors at
# 8s, 12s, 20s or 30s; across three trials the first anchor appeared
# at 46s, 50s and 49s. The matcher was therefore returning a confident
# zero against an unhydrated document. The schema confines those
# overrides to ``pre_filter_urls`` boards, which is exactly the runtime
# path that reads these constants, so raising a board's settle moves
# its capture and its runtime together and the byte-matching above
# still holds.
RENDER_WAIT_SEC: int = 8
RENDER_SCROLL_COUNT: int = 3

# Navigation ceiling for the capture-side tools, which call Playwright's
# ``page.goto`` directly. Playwright defaults to 30s and waits for the
# ``load`` event. A board slow enough to need a raised
# ``RuntimeHooks.render_wait_sec`` can also be slow to fire ``load``,
# and then the capture dies on navigation before the settle it was
# configured with ever runs. Edwards Lifesciences is the case, but
# intermittently: measured over four cold/warm samples its ``load``
# arrived at 51.7s, 2.9s, 2.2s and 2.8s. Only the cold load exceeds
# Playwright's default, which is precisely why a fixed 30s ceiling is
# the wrong shape — it converts an occasional slow start into a hard
# failure. Resolved as ``max(this, settle + margin)`` at the call
# sites rather than a flat constant, so a board that raises its settle
# raises its navigation ceiling with it and the two cannot drift.
# The runtime is unaffected: browser-use's ``navigate_to`` does not
# impose this ceiling (Edwards runs green at ~78s end to end).
NAVIGATION_TIMEOUT_SEC: int = 30
NAVIGATION_TIMEOUT_MARGIN_SEC: int = 30


def navigation_timeout_ms(wait_s: int) -> int:
    """Return the ``page.goto`` timeout in ms for a given settle.

    ``max(NAVIGATION_TIMEOUT_SEC, wait_s + NAVIGATION_TIMEOUT_MARGIN_SEC)``
    — never below Playwright's own default, and always comfortably
    above the configured settle so the navigation ceiling scales with
    the board rather than capping it.
    """
    return max(NAVIGATION_TIMEOUT_SEC, wait_s + NAVIGATION_TIMEOUT_MARGIN_SEC) * 1000


# ---------------------------------------------------------------------------
# browser-environment layer — plausible User-Agent
# ---------------------------------------------------------------------------
#
# Dev.Pro's WAF returns HTTP 403 to any browser whose User-Agent string
# contains the substring ``HeadlessChrome`` (C14 in
# ``blockers/INTEGRATION_BLOCKERS.md``). The three-way isolation
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
    # ``vacantes.settings`` (a leaf module imported by the schema,
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


# ---------------------------------------------------------------------------
# browser-environment layer — capture-side launch configuration
# ---------------------------------------------------------------------------
#
# The UA helpers above close C14, where a WAF greps the UA header for
# ``HeadlessChrome``. Two corpus boards reject the capture-side tools
# for reasons the UA cannot reach, and they fail differently:
#
# - **Edwards Lifesciences** times out on ``goto``. It is detecting the
#   automation surface: adding
#   ``--disable-blink-features=AutomationControlled`` (which clears
#   ``navigator.webdriver``) is sufficient on its own.
# - **McKinsey & Company** returns ``net::ERR_HTTP2_PROTOCOL_ERROR``
#   before any page code runs. The flag does *not* help; only a
#   different binary does. Playwright's bundled Chromium is refused
#   while Google Chrome (``channel="chrome"``) and browser-use's own
#   Chromium 134 both connect, so the rejection keys on something about
#   the bundled build rather than on automation signals. The precise
#   discriminator was not isolated — this records what was measured,
#   not a TLS-fingerprint theory.
#
# Hence two settings rather than one: the flag fixes Edwards, the
# channel fixes McKinsey, and only both together reach all three of
# Edwards, McKinsey, and the unaffected control boards.
#
# These apply to the **capture-side** launch sites (``capture_snapshot``
# and ``probe_board``), which drive Playwright directly. The runtime and
# the ground-truth diagnostic go through browser-use's
# ``BrowserSession``, which owns its own flag set and already reaches
# both hosts — so "all launch sites agree" holds for the UA and the
# keychain suppression, but the capture-side tools additionally opt
# into a real-Chrome channel. Keeping that asymmetry explicit here is
# the point of this block.
BROWSER_LAUNCH_ARGS: tuple[str, ...] = (
    "--password-store=basic",
    "--use-mock-keychain",
    "--disable-blink-features=AutomationControlled",
)

# Playwright browser channel the capture-side tools prefer. ``"chrome"``
# resolves to a locally installed Google Chrome. It is a *preference*,
# not a requirement: :func:`launch_capture_browser` falls back to the
# bundled Chromium when Chrome is absent, because every board except
# McKinsey captures fine either way and a missing Chrome must not break
# the other 106.
CAPTURE_BROWSER_CHANNEL: str = "chrome"


class CaptureBrowser(NamedTuple):
    """A launched capture browser paired with the UA it should present.

    The two travel together on purpose. :func:`plausible_headless_ua`
    derives its value from Playwright's *bundled* Chromium, which is a
    different binary from the one :data:`CAPTURE_BROWSER_CHANNEL`
    launches — pairing a bundled-derived UA with a real-Chrome engine
    advertises one major version while running another, which is
    exactly the kind of inconsistency bot management looks for. Making
    the launch hand back its own UA removes the opportunity to mismatch
    them rather than relying on every call site to remember.
    """

    browser: Browser
    user_agent: str


async def launch_capture_browser(
    playwright: Playwright, *, headless: bool = True
) -> CaptureBrowser:
    """Launch the browser the capture-side tools share, with its UA.

    Prefers :data:`CAPTURE_BROWSER_CHANNEL` and falls back to
    Playwright's bundled Chromium when that channel is not installed,
    logging a warning that names the consequence. The fallback is a
    real degradation — McKinsey's board is unreachable under the
    bundled build — so it is warned about rather than silent, but it is
    not fatal: a machine without Chrome can still capture every other
    board in the corpus.

    The returned UA is read from the launched instance and passed
    through :func:`derive_plausible_ua`, so it tracks whichever binary
    actually started — including across the fallback, where the answer
    differs. That keeps the C14 ``HeadlessChrome`` strip in force while
    closing the version-mismatch the module-level
    :func:`plausible_headless_ua` cache would otherwise introduce here.

    Args:
        playwright: An entered ``async_playwright()`` context.
        headless: Forwarded to ``chromium.launch``.

    Returns:
        A :class:`CaptureBrowser`. The caller owns closing ``browser``.
    """
    # Local import for the same reason ``plausible_headless_ua`` uses
    # one: ``vacantes.settings`` is a leaf module imported by the schema,
    # the catalog, the CLI and the reporting layer, and must not drag
    # Playwright in at import time. Only the launch sites — which have
    # already paid that cost — reach this function.
    from playwright.async_api import Error as PlaywrightError

    args = list(BROWSER_LAUNCH_ARGS)
    try:
        browser = await playwright.chromium.launch(
            channel=CAPTURE_BROWSER_CHANNEL, headless=headless, args=args
        )
    except PlaywrightError as exc:
        # Narrow on purpose: a missing channel raises
        # ``BrowserType.launch: Unsupported chromium channel "..."``.
        # Catching bare ``Exception`` here would relabel an unrelated
        # launch failure (bad args, sandbox refusal) as "Chrome not
        # installed" and then retry it identically, losing the real
        # error behind a misleading warning.
        logger.warning(
            "Chromium channel %r unavailable (%s); falling back to the "
            "bundled build. Boards that reject it — currently McKinsey & "
            "Company — will fail to load.",
            CAPTURE_BROWSER_CHANNEL,
            exc,
        )
        browser = await playwright.chromium.launch(headless=headless, args=args)

    page = await browser.new_page()
    try:
        default_ua: str = await page.evaluate("() => navigator.userAgent")
    finally:
        await page.close()
    return CaptureBrowser(browser=browser, user_agent=derive_plausible_ua(default_ua))
