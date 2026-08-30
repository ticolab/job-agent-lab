"""Shared pytest fixtures for the snapshot regression harness.

The pytest-playwright plugin provides session-scoped ``browser`` and
function-scoped ``page`` fixtures by default. The overrides below match
the Chromium launch arguments used by the production agent so that the
test environment is structurally identical to the live extractor's
environment, and disable page JavaScript so embedded snapshot scripts
cannot mutate the captured DOM at test time.
"""

from __future__ import annotations

from typing import Any

import pytest


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
