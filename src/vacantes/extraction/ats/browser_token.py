"""Borrow a bearer token a tenant's own page minted (the browser-token path, C19 seam).

Some tenants put their token-mint endpoint behind fingerprint-based bot
management. UST is the motivating case: ``www.ust.com`` answers **403**
to every :mod:`httpx` request — including a plain ``GET`` of the careers
page, and including one carrying a real browser's complete header set —
so the Coveo adapter cannot mint a token for itself no matter how its
request is shaped. That is blocker class C19, and it defeats *every*
non-browser strategy rather than any one adapter.

**Read, do not re-issue.** The load-bearing distinction is what kind of
operation crosses the gate. The C19 investigation established an
asymmetry that reads as a contradiction until it is stated precisely:

- a page-initiated XHR during a normal load returns ``200``;
- a ``page.evaluate`` fetch of that *identical URL*, issued from inside
  the very page that just succeeded, returns ``403``;
- reading the value the first request already deposited in
  ``sessionStorage`` is not a request at all, and is not gated.

This module does only the third thing. It never fetches the mint URL,
which is why it is not simply "the httpx mint with a browser attached" —
that approach was measured and refused. Any future change here must
preserve that property: the moment this code issues the gated request
from anywhere, browser included, it stops working.

Scope is deliberately one function. The helper is strategy-agnostic
(it takes a URL and a key, and knows nothing about Coveo) so a second
consumer needs no refactor, but it is lazily imported by its single
caller today so the rest of :mod:`vacantes.extraction.ats` stays
importable — and unit-testable — without the browser stack.

**Why the launch recipe is copied rather than shared.** The profile
below mirrors
:meth:`~vacantes.extraction.dom.strategy.DomStrategy._extract_prefiltered`
exactly: the plausible User-Agent and the two Chromium flags that
suppress the macOS keychain prompt. The UA is not cosmetic here — it is
the C14 closure, and a default headless UA advertising ``HeadlessChrome``
is precisely what a bot-management gate greps for. Every launch site in
this codebase must go through :func:`plausible_headless_ua`; this is now
one of them.

Failure is loud and informative. A missing key raises :class:`ValueError`
naming both the key that was expected and the keys actually present, so
a tenant-side rename reads as a diagnosis rather than a mystery. The
caller (:meth:`CoveoStrategy.extract`) folds that into its error report,
so a failure here degrades to an honest ``metadata.error`` rather than a
crash — the failure semantics every adapter in this package shares.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from browser_use.browser.profile import BrowserProfile
from browser_use.browser.session import BrowserSession

from vacantes.settings import plausible_headless_ua

logger = logging.getLogger(__name__)

# Bounded poll for the page's own mint XHR to land. The observed token
# appears on the first tick; the extra attempts buy tolerance for a slow
# network rather than for a genuinely absent key, which is why the loop
# gives up loudly instead of returning empty.
_POLL_ATTEMPTS = 6
_POLL_INTERVAL_SEC = 4

# Read-only probes. Neither issues a network request; both are plain
# reads of state the page already populated.
_READ_KEY_JS = "() => sessionStorage.getItem(%s)"
_LIST_KEYS_JS = "() => JSON.stringify(Object.keys(sessionStorage))"


def extract_token(raw: str) -> str:
    """Return the bare JWT from a ``sessionStorage`` value.

    UST stores the token bare, but the mint *response* it came from is
    the envelope ``{"token": "<JWT>"}`` (see the recorded fixture
    ``tests/fixtures/api/coveo/ust_token.json``). Since the page chooses
    what to persist, a tenant storing the whole envelope is a plausible
    variant rather than a hypothetical, so both shapes are accepted.

    Kept a module-level pure function so the unwrap rule is unit-testable
    without a browser — the same split as ``talentbrew.build_query_params``.

    Args:
        raw: The raw ``sessionStorage`` string.

    Returns:
        The bare token.

    Raises:
        ValueError: If an envelope is present but carries no non-empty
            string ``token``. Returning the envelope verbatim would send
            a JSON blob as a bearer credential and fail later, further
            from the cause.
    """
    candidate = raw.strip()
    if not candidate.startswith("{"):
        return candidate

    try:
        parsed = json.loads(candidate)
    except ValueError:
        # Not JSON after all — treat it as an opaque token rather than
        # second-guessing a tenant whose token merely starts with "{".
        return candidate

    if not isinstance(parsed, dict):
        return candidate

    token = parsed.get("token")
    if not isinstance(token, str) or not token:
        raise ValueError(
            "sessionStorage value parsed as a JSON object but carries no "
            f"non-empty string 'token' (keys={sorted(parsed)!r})."
        )
    return token


async def _read_key(page: Any, key: str) -> str | None:
    """Read one ``sessionStorage`` key, tolerating both page APIs.

    browser-use's ``Page.evaluate`` stringifies primitives, so a genuine
    JS ``null`` can arrive as the *string* ``"null"``. Both are treated
    as absent — the same defensive parse
    :class:`~vacantes.extraction.dom.collector.ActorPageDriver`
    performs for the matcher.
    """
    raw = await page.evaluate(_READ_KEY_JS % json.dumps(key))
    if raw is None:
        return None
    if not isinstance(raw, str):
        return str(raw)
    if raw in ("", "null", "undefined"):
        return None
    return raw


async def read_session_storage_token(url: str, key: str, *, headless: bool) -> str:
    """Load ``url`` in Chromium and return the token stored under ``key``.

    Navigates once, then polls ``sessionStorage`` until the page's own
    mint XHR has landed. Never requests the mint endpoint itself — see
    the module docstring for why that distinction is the whole point.

    Args:
        url: Page to load; the tenant page whose script mints the token.
            For Coveo tenants this is ``Company.job_board_url``.
        key: The ``sessionStorage`` key holding the token
            (``CoveoConfig.browser_token_key``).
        headless: Mirrors ``RunContext.headless`` so ``--headed`` shows
            this load too.

    Returns:
        The bare bearer token.

    Raises:
        ValueError: If the key never appears, or holds an envelope with
            no usable token. The message names the keys that *were*
            present, which is the diagnosis for a tenant-side rename.
    """
    user_agent = await plausible_headless_ua()
    browser_profile = BrowserProfile(
        headless=headless,
        user_agent=user_agent,
        args=["--password-store=basic", "--use-mock-keychain"],
    )
    browser_session = BrowserSession(browser_profile=browser_profile)

    present: str = "<unread>"
    try:
        await browser_session.start()
        await browser_session.navigate_to(url)

        for attempt in range(1, _POLL_ATTEMPTS + 1):
            await asyncio.sleep(_POLL_INTERVAL_SEC)
            page = await browser_session.get_current_page()
            if page is None:
                raise ValueError(
                    f"BrowserSession returned no page after navigating to {url}"
                )
            raw = await _read_key(page, key)
            if raw is not None:
                logger.info(
                    "Borrowed token from sessionStorage[%r] on attempt %s", key, attempt
                )
                return extract_token(raw)
            present = str(await page.evaluate(_LIST_KEYS_JS))
            logger.warning(
                "sessionStorage[%r] not populated on attempt %s/%s for %s",
                key,
                attempt,
                _POLL_ATTEMPTS,
                url,
            )
    finally:
        await browser_session.stop()

    raise ValueError(
        f"sessionStorage[{key!r}] was never populated after "
        f"{_POLL_ATTEMPTS} attempts ({_POLL_ATTEMPTS * _POLL_INTERVAL_SEC}s) "
        f"on {url}. Keys present: {present}. If the tenant renamed the key, "
        f"update CoveoConfig.browser_token_key; if the page never minted a "
        f"token, the load itself may have been challenged."
    )
