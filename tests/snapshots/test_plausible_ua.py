"""Real-Chromium checks for ``settings.plausible_headless_ua``.

The byte-exact ``HeadlessChrome/`` → ``Chrome/`` transform is pinned by
the browser-free tests in ``tests/unit/test_settings.py``. These tests
exercise the async resolver against an actual headless launch, to
confirm the UA the runtime presents to origins does not carry the token
the C14 WAF greps for (see ``blockers/INTEGRATION_BLOCKERS.md``). They
live in this suite rather than beside the transform tests because they
need an installed Chromium, the same cost class as the snapshot
replays, and ``tests/unit/`` must run without one.

The resolver launches Chromium through Playwright's async API, so each
call is driven by ``run_async`` on a background thread's own loop: the
sync API this suite's other tests use leaves the main thread unable to
run an event loop.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from job_discovery import settings
from job_discovery.settings import plausible_headless_ua
from tests.unit.asyncio_harness import run_async


class TestPlausibleHeadlessUa:
    """Integration checks against a real Playwright Chromium launch."""

    @pytest.fixture(autouse=True)
    def _reset_cache(self) -> Iterator[None]:
        # Each test in this class exercises cache behaviour explicitly,
        # so start each one from a cold cache. The autouse fixture is
        # scoped to this class only — other tests that import
        # ``settings`` should never observe a mutation.
        settings._PLAUSIBLE_UA = None
        yield
        settings._PLAUSIBLE_UA = None

    def test_resolver_strips_headless_token(self) -> None:
        ua = run_async(plausible_headless_ua())
        assert isinstance(ua, str)
        assert "Chrome/" in ua
        assert "HeadlessChrome" not in ua

    def test_second_call_returns_cached_instance(self) -> None:
        first = run_async(plausible_headless_ua())
        second = run_async(plausible_headless_ua())
        # Memoization is observable via ``is`` — the same ``str``
        # instance is returned on the second call, no re-launch of
        # Chromium.
        assert first is second
