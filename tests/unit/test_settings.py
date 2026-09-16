"""Unit + integration tests for ``vacantes.settings``'s UA helpers.

The pure derivation helper is the single "contract" every launch site
depends on: given a Chromium UA string, strip the
``HeadlessChrome/<v>`` product token so the WAF class described in
``blockers/INTEGRATION_BLOCKERS.md`` (C14) does not 403 the request.
The unit-level tests lock in the byte-exact transform, and the
integration test exercises the async resolver against a real Chromium
launch to confirm the actual UA the runtime will present to origins
does not carry the ``HeadlessChrome`` token.

The async integration tests follow the same pattern as
``tests/unit/test_greenhouse.py``: coroutines run in a worker thread's
fresh event loop rather than via ``asyncio.run`` on the main thread,
because ``pytest-playwright`` (loaded by the snapshot suite) leaves a
non-``None`` entry in the main thread's ``asyncio.events`` thread-local
that both entry points refuse to run under. This keeps the repo free
of an extra ``pytest-asyncio`` dependency.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from vacantes import settings
from vacantes.settings import (
    BROWSER_LAUNCH_ARGS,
    CAPTURE_BROWSER_CHANNEL,
    DATABASE_ENV_VAR,
    DEFAULT_DATABASE_PATH,
    database_path_from_env,
    derive_plausible_ua,
    launch_capture_browser,
    plausible_headless_ua,
)

# Representative Chromium headless UA at the time of the C14 isolation
# experiment (Dev.Pro; see C14 in ``blockers/INTEGRATION_BLOCKERS.md``).
# Fixed literal on purpose: the test locks the transform's byte-exact
# behaviour, so version churn in Playwright's bundled Chromium must not
# invalidate the golden.
_HEADLESS_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "HeadlessChrome/126.0.0.0 Safari/537.36"
)
_EXPECTED_PLAUSIBLE_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def _run(coro: Any) -> Any:
    """Drive a coroutine to completion in a worker thread's fresh loop.

    Mirrors the helper in ``tests/unit/test_greenhouse.py`` — see the
    docstring there for the ``pytest-playwright`` interaction that
    forces the worker-thread indirection.
    """
    result: dict[str, Any] = {}

    def _worker() -> None:
        loop = asyncio.new_event_loop()
        try:
            result["value"] = loop.run_until_complete(coro)
        except BaseException as exc:  # noqa: BLE001 — re-raised on main
            result["error"] = exc
        finally:
            loop.close()

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result["value"]


class TestDerivePlausibleUa:
    """Pure-transform contract: ``HeadlessChrome/`` → ``Chrome/``.

    All assertions use full-string equality (not substring
    containment): the WAF at C14 greps for the literal token, and any
    accidental corruption of the surrounding bytes would silently
    regress every existing corpus board.
    """

    def test_token_is_replaced_full_string(self) -> None:
        # Full-string equality — asserts every byte outside the token is
        # preserved (version, platform triplet, WebKit build tag,
        # trailing ``Safari/537.36`` marker).
        assert derive_plausible_ua(_HEADLESS_UA) == _EXPECTED_PLAUSIBLE_UA

    def test_version_string_preserved_verbatim(self) -> None:
        # The version segment following the token slash must survive
        # untouched — no pinning, no drift as Playwright updates its
        # bundled Chromium.
        result = derive_plausible_ua(_HEADLESS_UA)
        assert "Chrome/126.0.0.0" in result
        assert "HeadlessChrome" not in result

    def test_headed_ua_passes_through_unchanged(self) -> None:
        # A headed launch's default UA does not contain the token, so
        # the transform is a byte-identical no-op — every launch site
        # can call the resolver unconditionally without regressing
        # headed behaviour.
        headed_ua = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        )
        assert derive_plausible_ua(headed_ua) == headed_ua

    def test_empty_string_is_noop(self) -> None:
        # Defensive: an empty input (should never happen at a real
        # launch site) must not raise.
        assert derive_plausible_ua("") == ""

    def test_no_token_leaks_when_version_absent(self) -> None:
        # A malformed UA carrying ``HeadlessChrome`` without a slash is
        # deliberately *not* stripped — the transform targets the exact
        # product-token form ``HeadlessChrome/`` and nothing else. This
        # keeps the transform local (no regex, no boundary heuristics)
        # and matches the WAF's own substring check.
        malformed = "Mozilla/5.0 HeadlessChrome Safari/537.36"
        assert derive_plausible_ua(malformed) == malformed


class TestPlausibleHeadlessUa:
    """Integration checks against a real Playwright Chromium launch.

    Marked P1 in the plan: same cost class as the snapshot suite
    (a real headless launch, ~0.5 s). The unit-level pure-transform
    tests above are the P0 fast path.
    """

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
        ua = _run(plausible_headless_ua())
        assert isinstance(ua, str)
        assert "Chrome/" in ua
        assert "HeadlessChrome" not in ua

    def test_second_call_returns_cached_instance(self) -> None:
        first = _run(plausible_headless_ua())
        second = _run(plausible_headless_ua())
        # Memoization is observable via ``is`` — the same ``str``
        # instance is returned on the second call, no re-launch of
        # Chromium.
        assert first is second


# ---------------------------------------------------------------------------
# Database path resolution
# ---------------------------------------------------------------------------


class TestDatabasePathFromEnv:
    """The one environment variable settings reads, as a pure function.

    Tested over an explicit mapping rather than by mutating the process
    environment and reloading the module, because every importer has
    already bound ``DATABASE_PATH`` by name and a reload would leave them
    holding the old object while the test observed a new one.
    """

    def test_unset_falls_back_to_the_default(self) -> None:
        assert database_path_from_env({}) == DEFAULT_DATABASE_PATH
        assert Path("data") / "vacantes.db" == DEFAULT_DATABASE_PATH

    def test_a_set_variable_wins(self) -> None:
        resolved = database_path_from_env({DATABASE_ENV_VAR: "/srv/vacantes/live.db"})
        assert resolved == Path("/srv/vacantes/live.db")

    def test_a_blank_variable_is_treated_as_unset(self) -> None:
        """``VACANTES_DB=`` must not resolve to a database named ``""``."""
        assert database_path_from_env({DATABASE_ENV_VAR: "  "}) == DEFAULT_DATABASE_PATH


class _FakePage:
    """Minimal ``Page`` stand-in exposing only what the launcher uses."""

    def __init__(self, ua: str) -> None:
        self._ua = ua
        self.closed = False

    async def evaluate(self, _script: str) -> str:
        return self._ua

    async def close(self) -> None:
        self.closed = True


class _FakeBrowser:
    def __init__(self, ua: str) -> None:
        self.ua = ua
        self.pages: list[_FakePage] = []

    async def new_page(self, **_kwargs: Any) -> _FakePage:
        page = _FakePage(self.ua)
        self.pages.append(page)
        return page


class _FakeChromium:
    """Records launch kwargs; optionally fails the channelled launch."""

    def __init__(self, *, channel_error: BaseException | None, ua: str) -> None:
        self._channel_error = channel_error
        self._ua = ua
        self.calls: list[dict[str, Any]] = []

    async def launch(self, **kwargs: Any) -> _FakeBrowser:
        self.calls.append(kwargs)
        if "channel" in kwargs and self._channel_error is not None:
            raise self._channel_error
        return _FakeBrowser(self._ua)


class _FakePlaywright:
    def __init__(self, chromium: _FakeChromium) -> None:
        self.chromium = chromium


class TestLaunchCaptureBrowser:
    """The capture-side launcher: channel preference, fallback, and UA source.

    Driven against a fake Playwright rather than a real browser. The
    behaviours worth pinning are policy, not rendering: which channel is
    tried first, what happens when it is missing, which exceptions are
    treated as "Chrome absent", and — the bug this class was written
    for — that the returned UA comes from the instance that actually
    launched rather than from the module-level
    :func:`plausible_headless_ua` cache, which is derived from a
    *different* binary and would advertise the wrong major version.
    """

    _CHROME_UA = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "HeadlessChrome/153.0.0.0 Safari/537.36"
    )

    def test_prefers_the_configured_channel(self) -> None:
        chromium = _FakeChromium(channel_error=None, ua=self._CHROME_UA)
        result = _run(launch_capture_browser(_FakePlaywright(chromium)))  # type: ignore[arg-type]
        assert len(chromium.calls) == 1
        assert chromium.calls[0]["channel"] == CAPTURE_BROWSER_CHANNEL
        assert result.browser is not None

    def test_standing_args_are_passed_on_both_paths(self) -> None:
        # The keychain suppression and the AutomationControlled flag must
        # survive the fallback; a board that only needed the flag (Edwards)
        # still has to work on a machine without Chrome.
        from playwright.async_api import Error as PlaywrightError

        for err in (None, PlaywrightError("Unsupported chromium channel")):
            chromium = _FakeChromium(channel_error=err, ua=self._CHROME_UA)
            _run(launch_capture_browser(_FakePlaywright(chromium)))  # type: ignore[arg-type]
            for call in chromium.calls:
                assert call["args"] == list(BROWSER_LAUNCH_ARGS)

    def test_falls_back_to_the_bundled_build_without_a_channel(self) -> None:
        from playwright.async_api import Error as PlaywrightError

        chromium = _FakeChromium(
            channel_error=PlaywrightError('Unsupported chromium channel "chrome"'),
            ua=self._CHROME_UA,
        )
        result = _run(launch_capture_browser(_FakePlaywright(chromium)))  # type: ignore[arg-type]
        assert len(chromium.calls) == 2
        assert "channel" in chromium.calls[0]
        assert "channel" not in chromium.calls[1]
        assert result.browser is not None

    def test_a_non_playwright_launch_failure_is_not_swallowed(self) -> None:
        # The except clause is narrowed to Playwright's own error so an
        # unrelated failure surfaces as itself instead of being relabelled
        # "Chrome not installed" and retried identically.
        chromium = _FakeChromium(channel_error=RuntimeError("disk full"), ua="x")
        with pytest.raises(RuntimeError, match="disk full"):
            _run(launch_capture_browser(_FakePlaywright(chromium)))  # type: ignore[arg-type]
        assert len(chromium.calls) == 1

    def test_ua_is_read_from_the_launched_instance_and_stripped(self) -> None:
        # The regression this guards: pairing a bundled-Chromium UA with a
        # real-Chrome engine. The returned UA must derive from the browser
        # handed back, with the C14 HeadlessChrome token removed.
        chromium = _FakeChromium(channel_error=None, ua=self._CHROME_UA)
        result = _run(launch_capture_browser(_FakePlaywright(chromium)))  # type: ignore[arg-type]
        assert result.user_agent == derive_plausible_ua(self._CHROME_UA)
        assert "HeadlessChrome" not in result.user_agent
        assert "153.0.0.0" in result.user_agent

    def test_the_ua_probe_page_is_closed(self) -> None:
        # The probe page is an artifact of reading the UA; leaving it open
        # would leak a tab into every capture run.
        chromium = _FakeChromium(channel_error=None, ua=self._CHROME_UA)
        result = _run(launch_capture_browser(_FakePlaywright(chromium)))  # type: ignore[arg-type]
        assert result.browser.pages[0].closed is True
