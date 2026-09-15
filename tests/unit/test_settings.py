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
from typing import Any

import pytest

from vacantes import settings
from vacantes.settings import derive_plausible_ua, plausible_headless_ua

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

    Marked P1 in the SYS-10 plan: same cost class as the snapshot suite
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
