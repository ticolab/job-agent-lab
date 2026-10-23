"""Shared pytest fixtures for the snapshot regression harness.

The pytest-playwright plugin provides session-scoped ``browser`` and
function-scoped ``page`` fixtures by default. The overrides below match
the Chromium launch arguments used by the production agent so that the
test environment is structurally identical to the live extractor's
environment, disable page JavaScript so embedded snapshot scripts
cannot mutate the captured DOM at test time, and keep every replay off
the network so a snapshot never depends on, or waits for, the live site.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from playwright.sync_api import BrowserContext

# Every request a replayed document can make: the stylesheets, images,
# iframes, and navigations its markup references on the live site, which
# the injected ``<base href>`` resolves there.
_NETWORK_URL = re.compile(r"^https?://")


@pytest.fixture(scope="session")
def browser_type_launch_args(
    browser_type_launch_args: dict[str, Any],
) -> dict[str, Any]:
    """Mirror production Chromium launch args (mute macOS keychain prompt)."""
    return {
        **browser_type_launch_args,
        "args": [
            *browser_type_launch_args.get("args", []),
            "--password-store=basic",
            "--use-mock-keychain",
        ],
    }


@pytest.fixture(scope="session")
def browser_context_args(
    browser_context_args: dict[str, Any],
) -> dict[str, Any]:
    """Disable page JS so snapshot ``<script>`` tags cannot rewrite the DOM.

    Each captured ``page.html`` is a serialised post-render snapshot whose
    ``<script>`` tags are preserved verbatim. When the test loads the HTML
    via ``page.set_content``, those scripts re-execute against a fresh
    document and frequently mutate or wipe the listing region (React-style
    rehydration, SPA bootstrappers replacing innerHTML, anti-scraping
    redirects, etc.). The matcher then races against the script: if it
    queries the DOM first it sees the captured anchors, otherwise it sees
    an empty document, producing a binary 0-vs-N flake.

    Disabling JS at the context level prevents the page's own scripts from
    running at all. Playwright's automation API (``page.evaluate`` in
    particular) keeps working because it injects code into an isolated CDP
    world, independent of the page's JS engine.
    """
    return {
        **browser_context_args,
        "java_script_enabled": False,
    }


@pytest.fixture(autouse=True)
def _replay_offline(request: pytest.FixtureRequest) -> None:
    """Answer every network request a browser test makes with ``204``.

    A snapshot is a frozen DOM, but its markup still points at the live
    site — Sequoia Connect's alone references 95 stylesheets — so before
    this a replay fetched about 1,850 live resources per run. That made
    the suite depend on third-party servers in two ways. A stalled
    resource held ``set_content`` until its 30 s timeout (Sequoia Connect
    in CI). And documents could navigate themselves: the iCIMS frame
    documents (Auxis, Avionyx) carry a zero-delay ``<meta
    http-equiv="refresh">`` to ``/jsDisabledError.jsp`` that fires because
    page JavaScript is disabled here, and whenever that response arrived
    in time, the next ``set_content`` lost its execution context — or the
    matcher would have counted the error page instead.

    ``204`` rather than an abort, because a navigation answered with 204
    is not committed and the replayed document stays put, while an
    aborted one is swapped for an error page and loses the context just
    the same. Subresources come back empty. Every fixture replays to its
    recorded count this way, so no count depended on the live site.

    Routes a test registers on its own ``page`` take precedence over this
    context-level one, so harnesses that serve fixtures by route keep
    working. Tests that never request the browser fixtures skip this.
    """
    if "context" not in request.fixturenames:
        return
    context: BrowserContext = request.getfixturevalue("context")
    context.route(_NETWORK_URL, lambda route: route.fulfill(status=204))
