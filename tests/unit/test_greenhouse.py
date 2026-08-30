"""Unit tests for the Greenhouse API strategy (SYS-4 Task 3).

Two layers of coverage:

- ``board_token`` — the extract-time belt to the ``Company`` schema
  validator's braces. Exercised for the happy path (canonical + legacy
  hosts) and each raise-worthy edge (non-greenhouse strategy, wrong
  host, empty path).

- ``GreenhouseStrategy.extract`` — the full runtime, driven through
  ``respx`` so no network is touched. Covered:

  * Per-tenant happy paths against **live-recorded** fixtures at
    ``tests/fixtures/api/greenhouse/{zscaler,movableink,westmonroe4}.json``.
    The expected result sets are pinned to recorded reality per SYS-4
    Decision 6 (fixture drift protocol): Zscaler 2 URLs (both
    ``Escazu, CRI``); Movable Ink 3 URLs pinned to gh_jid
    7383476/7395559/7315086; West Monroe 11 URLs (grew from the plan's
    AC of 9 by the time the fixtures were recorded — 2 new Costa Rica
    postings).
  * Retry policy: 500-then-500 → error report with empty jobs list;
    500-then-200 → retry success; transport-error-then-200 → retry
    success.
  * Payload-shape errors: missing ``jobs`` key, non-object body,
    non-list ``jobs`` — each folds into an error report; ``extract``
    never raises out.
  * Region-filter edge cases via a synthetic tenant: a compound
    location string (``Remote - Costa Rica; Austin``) matches; a
    tenant with no region-matching postings produces an empty result
    with ``error=None`` — an honest zero, not a broken board.

All tests use synchronous ``pytest`` functions that drive the async
:meth:`~job_agent_lab.extraction.ats.greenhouse.GreenhouseStrategy.extract`
via ``asyncio.run(...)``. This keeps the test file free of extra
async-runner plugins (the repo does not use ``pytest-asyncio``).
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from job_agent_lab.domain.company import Company
from job_agent_lab.domain.region import COSTA_RICA_LATAM
from job_agent_lab.extraction.ats.greenhouse import (
    GreenhouseStrategy,
    board_token,
)
from job_agent_lab.extraction.base import RunContext

# The public JSON-API host the strategy hits. Kept as a module-level
# constant here so the respx routes stay readable; the same value is
# defined privately in ``ats/greenhouse.py`` — if that ever changes,
# these tests will fail loudly (respx will 404 on the un-mocked host)
# rather than silently miss the assertion.
_API_BASE = "https://boards-api.greenhouse.io/v1/boards"

_FIXTURE_DIR = (
    Path(__file__).resolve().parent.parent / "fixtures" / "api" / "greenhouse"
)


def _load_fixture(name: str) -> dict[str, Any]:
    """Read a recorded Greenhouse ``/v1/boards/<token>/jobs`` payload."""
    payload = json.loads((_FIXTURE_DIR / f"{name}.json").read_text())
    assert isinstance(payload, dict), f"fixture {name!r} is not a JSON object"
    return payload


def _run(coro: Any) -> Any:
    """Drive a coroutine to completion in a worker thread's fresh loop.

    Cannot use ``asyncio.run(...)`` or ``loop.run_until_complete(...)``
    on the pytest main thread: ``pytest-playwright`` (loaded by the
    sibling snapshot suite) leaves an entry in
    ``asyncio.events._get_running_loop()`` on the test thread that
    survives even after its own tests finish. Both entry points
    check that thread-local and refuse to run when it is non-``None``.

    Running the coroutine in a fresh thread sidesteps the leak
    entirely: each thread has its own asyncio thread-local state
    that starts as ``None``, so a brand-new loop can be created,
    driven to completion, and closed with no interference from
    Playwright's leftovers. The thread lives only for the duration
    of one coroutine call, so this stays cheap.
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


def _make_company(token: str) -> Company:
    """Build a Greenhouse-strategy ``Company`` for tests."""
    return Company(
        name=f"Test-{token}",
        job_board_url=f"https://job-boards.greenhouse.io/{token}",
        # ``sample_job_url`` is unused by the greenhouse strategy but
        # required by the schema; use a URL under the canonical host
        # to keep the fixture obviously synthetic.
        sample_job_url=f"https://job-boards.greenhouse.io/{token}/jobs/1",
        strategy="greenhouse",
    )


def _ctx() -> RunContext:
    """Build a default ``RunContext`` for the strategy under test.

    The DOM-only knobs (``model``, ``headless``, ``max_steps``) are
    ignored by :class:`GreenhouseStrategy`, so use trivially recognisable
    sentinel values — if any of them ever ends up in the API-strategy
    report, the null-checks in the assertions catch it.
    """
    return RunContext(
        model="unused-model",
        headless=True,
        max_steps=1,
        region=COSTA_RICA_LATAM,
    )


# ---------------------------------------------------------------------------
# board_token
# ---------------------------------------------------------------------------


class TestBoardToken:
    """Extract-time belt for the Greenhouse host / token convention."""

    def test_canonical_host(self) -> None:
        c = _make_company("zscaler")
        assert board_token(c) == "zscaler"

    def test_legacy_host(self) -> None:
        c = Company(
            name="Legacy",
            job_board_url="https://boards.greenhouse.io/example",
            sample_job_url="https://boards.greenhouse.io/example/jobs/1",
            strategy="greenhouse",
        )
        assert board_token(c) == "example"

    def test_trailing_slash_and_extra_segments_take_first(self) -> None:
        # The API only cares about the tenant slug (first segment);
        # additional path segments on the ``Company.job_board_url``
        # (e.g. ``/jobs``) must not corrupt the extracted token.
        c = Company(
            name="Extra Path",
            job_board_url="https://job-boards.greenhouse.io/westmonroe4/jobs",
            sample_job_url="https://job-boards.greenhouse.io/westmonroe4/jobs/1",
            strategy="greenhouse",
        )
        assert board_token(c) == "westmonroe4"

    def test_wrong_strategy_raises(self) -> None:
        # A Company with strategy='dom' should never reach ``board_token``.
        # If it does (test typo, mis-classified entry), fail loudly.
        c = Company(
            name="DomEntry",
            job_board_url="https://example.com/careers",
            sample_job_url="https://example.com/jobs/1",
        )
        with pytest.raises(ValueError, match="strategy='greenhouse'"):
            board_token(c)

    def test_wrong_host_raises(self) -> None:
        # The schema validator already blocks this at construction
        # time, so we have to build the Company as ``strategy="dom"``
        # (bypassing the validator) and then hand-mutate the strategy
        # via ``model_copy(update=...)`` — pydantic's frozen-update
        # semantics — to exercise the belt.
        base = Company(
            name="Marketing Only",
            job_board_url="https://acme.example.com/careers",
            sample_job_url="https://acme.example.com/jobs/1",
        )
        forged = base.model_copy(update={"strategy": "greenhouse"})
        with pytest.raises(ValueError, match="host in "):
            board_token(forged)


# ---------------------------------------------------------------------------
# Recorded-fixture happy paths (per tenant)
# ---------------------------------------------------------------------------


class TestExtractHappyPathRecorded:
    """Per-tenant expected URLs, pinned to recorded fixtures.

    The numbers below are recorded reality as of SYS-4 Task 3
    fixture capture (see Decision 6 in ``spike/SYS_4_PLAN.md``):
    Zscaler 2, Movable Ink 3, West Monroe 11. The plan AC listed
    9 for West Monroe; the board grew by 2 postings between AC
    authoring and fixture capture — see the SYS-4 Task 3 commit
    message for the drift record. Elastic 3 was added post-SYS-4
    when Elastic was integrated (queue ``expected_jobs=3``, matches
    recorded payload's CR count exactly).
    """

    def test_zscaler(self) -> None:
        payload = _load_fixture("zscaler")
        with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{_API_BASE}/zscaler/jobs").mock(
                return_value=httpx.Response(200, json=payload)
            )
            result = _run(
                GreenhouseStrategy().extract(_make_company("zscaler"), _ctx())
            )

        assert result["metadata"]["error"] is None
        assert result["metadata"]["strategy"] == "greenhouse"
        assert result["metadata"]["model"] is None
        assert result["metadata"]["agent_steps"] is None
        assert result["metadata"]["agent_completed"] is None
        assert result["metadata"]["agent_had_errors"] is None
        assert result["jobs"] == [
            "https://job-boards.greenhouse.io/zscaler/jobs/5103265007",
            "https://job-boards.greenhouse.io/zscaler/jobs/5141020007",
        ]

    def test_movableink_pinned_gh_jids(self) -> None:
        payload = _load_fixture("movableink")
        with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{_API_BASE}/movableink/jobs").mock(
                return_value=httpx.Response(200, json=payload)
            )
            result = _run(
                GreenhouseStrategy().extract(_make_company("movableink"), _ctx())
            )

        assert result["metadata"]["error"] is None
        # Movable Ink's ``absolute_url`` points at the marketing site
        # with a gh_jid query param — pin the exact ids.
        assert result["jobs"] == [
            "https://movableink.com/job-listing?gh_jid=7383476",
            "https://movableink.com/job-listing?gh_jid=7395559",
            "https://movableink.com/job-listing?gh_jid=7315086",
        ]

    def test_westmonroe4_recorded_eleven(self) -> None:
        # Plan AC was 9; recorded reality at fixture-capture time is 11.
        # Per Decision 6 we pin to reality and document the +2 drift
        # in the commit — DoM (Task 3) explicitly permits this.
        payload = _load_fixture("westmonroe4")
        with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{_API_BASE}/westmonroe4/jobs").mock(
                return_value=httpx.Response(200, json=payload)
            )
            result = _run(
                GreenhouseStrategy().extract(_make_company("westmonroe4"), _ctx())
            )

        assert result["metadata"]["error"] is None
        assert len(result["jobs"]) == 11
        # Every returned URL must be an ``absolute_url`` from the
        # fixture — spot-check that they share the marketing host and
        # the gh_jid query param (West Monroe's URL shape).
        for url in result["jobs"]:
            assert url.startswith("https://westmonroe.com/careers/")
            assert "gh_jid=" in url

    def test_elastic_recorded_three(self) -> None:
        # Elastic's Greenhouse tenant hosts 177 postings at fixture
        # capture time; 3 carry ``location.name == "Costa Rica"`` and
        # the region filter keeps exactly those. The queue's
        # ``expected_jobs=3`` matches recorded reality — no drift.
        #
        # Elastic's ``absolute_url`` has a tenant-configured quirk:
        # the ``gh_jid`` query parameter is doubled
        # (``?gh_jid=X&gh_jid=X``). The strategy emits ``absolute_url``
        # verbatim from the payload (SYS-4 contract), so the doubled
        # form is pinned here as recorded reality — it is *not* a bug
        # in the strategy and the test would fail loudly if the
        # strategy ever started to normalise the URL.
        payload = _load_fixture("elastic")
        with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{_API_BASE}/elastic/jobs").mock(
                return_value=httpx.Response(200, json=payload)
            )
            result = _run(
                GreenhouseStrategy().extract(_make_company("elastic"), _ctx())
            )

        assert result["metadata"]["error"] is None
        # Payload order is preserved — Elastic's payload happens to
        # list the CR postings as gh_jid 8065315, 7875644, 8031519.
        assert result["jobs"] == [
            "https://jobs.elastic.co/jobs?gh_jid=8065315&gh_jid=8065315",
            "https://jobs.elastic.co/jobs?gh_jid=7875644&gh_jid=7875644",
            "https://jobs.elastic.co/jobs?gh_jid=8031519&gh_jid=8031519",
        ]

    def test_newsela_recorded_two(self) -> None:
        # Newsela's Greenhouse tenant hosts 16 postings at fixture
        # capture time; 2 carry a compound-LATAM ``location.name``
        # that includes ``Costa Rica`` and the region filter keeps
        # exactly those. The queue's ``expected_jobs=16`` reflects
        # the *tenant total*, not the CR-filtered count — a drift
        # from the L11 queue-authoring convention (queue values are
        # supposed to be the filtered target). Per SYS-4 Decision 6
        # we pin to recorded reality and document the drift in the
        # commit; the test asserts 2, not 16.
        #
        # Both matches are pan-LATAM remote roles whose
        # ``location.name`` is a compound string listing multiple
        # countries with Costa Rica among them; the substring
        # match in ``TargetRegion.matches`` correctly recognises
        # ``Costa Rica`` inside the compound name (see
        # ``test_compound_location_matches_costa_rica`` for the
        # semantics on a synthetic payload).
        payload = _load_fixture("newsela")
        with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{_API_BASE}/newsela/jobs").mock(
                return_value=httpx.Response(200, json=payload)
            )
            result = _run(
                GreenhouseStrategy().extract(_make_company("newsela"), _ctx())
            )

        assert result["metadata"]["error"] is None
        # Payload order preserved — Newsela lists 8010541 first,
        # then 7818607. ``absolute_url`` points at the canonical
        # Greenhouse-hosted job page (no marketing-host rewrite,
        # unlike Movable Ink or Elastic).
        assert result["jobs"] == [
            "https://job-boards.greenhouse.io/newsela/jobs/8010541",
            "https://job-boards.greenhouse.io/newsela/jobs/7818607",
        ]

    def test_armissecurity_recorded_one(self) -> None:
        # Armis's Greenhouse tenant (token ``armissecurity``, distinct
        # from the display name ``Armis``) hosts 29 postings at
        # fixture capture time; exactly 1 carries the hard-CR
        # structured ``location.name`` ``San José, San José,
        # Costa Rica`` (city, province, country — the same three-part
        # shape Zscaler uses for ``Escazu, CRI``). Distinct from
        # Newsela's two compound-LATAM strings — Armis has a real
        # single-country CR posting.
        #
        # Queue drift: ``expected_jobs=11`` matches neither the
        # tenant total (29) nor the CR-filtered count (1). Unlike
        # Newsela's tenant-total drift, this is a third drift shape
        # — likely stale (a snapshot from months earlier when Armis
        # had more CR postings) or a broader-region filter at
        # queue-authoring time. Per SYS-4 Decision 6 the test pins
        # to recorded reality: 1, not 11.
        payload = _load_fixture("armissecurity")
        with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{_API_BASE}/armissecurity/jobs").mock(
                return_value=httpx.Response(200, json=payload)
            )
            result = _run(
                GreenhouseStrategy().extract(_make_company("armissecurity"), _ctx())
            )

        assert result["metadata"]["error"] is None
        assert result["jobs"] == [
            "https://job-boards.greenhouse.io/armissecurity/jobs/5785560004",
        ]

    def test_report_shape_shared_with_dom(self) -> None:
        # Independent of tenant: the API strategy's report must expose
        # the same top-level + metadata keys as the DOM strategy's
        # (the shape is authored by ``build_report``). Locked here so a
        # divergence at either end shows up in Greenhouse tests too.
        payload = _load_fixture("zscaler")
        with respx.mock() as mock:
            mock.get(f"{_API_BASE}/zscaler/jobs").mock(
                return_value=httpx.Response(200, json=payload)
            )
            result = _run(
                GreenhouseStrategy().extract(_make_company("zscaler"), _ctx())
            )

        assert set(result) == {"company", "url", "jobs", "metadata"}
        assert set(result["metadata"]) == {
            "strategy",
            "model",
            "total_jobs_found",
            "extraction_time_seconds",
            "agent_steps",
            "agent_completed",
            "agent_had_errors",
            "error",
            "expected_jobs",
            "verdict",
        }
        assert result["metadata"]["total_jobs_found"] == len(result["jobs"])

    def test_varicent_recorded_four(self) -> None:
        # Varicent's tenant hosts 44 postings at fixture capture time;
        # 4 carry a Costa Rica ``location.name`` and the region filter
        # keeps exactly those.
        #
        # This tenant is the clearest case for why the strategy reads
        # ``location.name`` rather than the board's own office facet.
        # Varicent posts one requisition per country and files the
        # variants under a shared office, so the board's "Costa Rica"
        # office (``?offices[]=4029545008``) returns 7 — and it is
        # wrong in both directions. It *includes* four postings whose
        # own titles read "Mexico Only - Remote" and "Argentina Only -
        # Remote", and it *excludes* a genuine Costa Rica posting
        # (Multimedia Designer, ``location.name`` "San Jose, Costa
        # Rica"). The queue was authored from that facet at 7; the
        # location field gives the honest 4, which is what this pins.
        #
        # The four locations are deliberately heterogeneous —
        # "San José, Costa Rica" (accented), "San Jose, Costa Rica"
        # (unaccented), "San Jose, Costa Rica (Remote)", and
        # "Costa Rica - Remote" — so this case also exercises the
        # region predicate's tolerance of tenant-authored formatting.
        payload = _load_fixture("varicent")
        with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{_API_BASE}/varicent/jobs").mock(
                return_value=httpx.Response(200, json=payload)
            )
            result = _run(
                GreenhouseStrategy().extract(_make_company("varicent"), _ctx())
            )

        assert result["metadata"]["error"] is None
        assert result["jobs"] == [
            "https://job-boards.greenhouse.io/varicent/jobs/5208922008",
            "https://job-boards.greenhouse.io/varicent/jobs/5370595008",
            "https://job-boards.greenhouse.io/varicent/jobs/5310643008",
            "https://job-boards.greenhouse.io/varicent/jobs/5372097008",
        ]

    def test_cloudbeds_recorded_nine(self) -> None:
        # Cloudbeds' tenant hosts 47 postings at fixture capture time;
        # 9 carry "Latin America" in location.name — 7 solo and 2 in
        # compound strings ("Latin America; North America",
        # "Europe; Latin America; North America") — and the region
        # filter keeps exactly those.
        #
        # This tenant has no Costa Rica postings at all: the entire
        # in-region set is regional LATAM roles, which is why the
        # compound-location handling is load-bearing here rather than
        # incidental.
        #
        # Like Varicent, the board's own office facet is the wrong
        # surface: its "Latin America" office returns 15, including
        # three roles located "North America" only (Director of
        # Customer Support, Senior Backend Engineer, Senior Frontend
        # Engineer). The location field gives the honest 9.
        payload = _load_fixture("cloudbeds")
        with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{_API_BASE}/cloudbeds/jobs").mock(
                return_value=httpx.Response(200, json=payload)
            )
            result = _run(
                GreenhouseStrategy().extract(_make_company("cloudbeds"), _ctx())
            )

        assert result["metadata"]["error"] is None
        assert len(result["jobs"]) == 9
        by_url = {j["absolute_url"]: j for j in payload["jobs"]}
        for url in result["jobs"]:
            assert "Latin America" in by_url[url]["location"]["name"]

    def test_cloudbeds_compound_locations_are_kept(self) -> None:
        # The two compound-location postings are the ones a naive
        # equality check against "Latin America" would drop, so pin
        # them explicitly.
        payload = _load_fixture("cloudbeds")
        with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{_API_BASE}/cloudbeds/jobs").mock(
                return_value=httpx.Response(200, json=payload)
            )
            result = _run(
                GreenhouseStrategy().extract(_make_company("cloudbeds"), _ctx())
            )

        by_url = {j["absolute_url"]: j for j in payload["jobs"]}
        compound = {
            by_url[u]["location"]["name"]
            for u in result["jobs"]
            if ";" in by_url[u]["location"]["name"]
        }
        assert compound == {
            "Latin America; North America",
            "Europe; Latin America; North America",
        }

    def test_varicent_excludes_other_country_postings(self) -> None:
        # Guard the specific failure the office facet would have
        # produced: none of the emitted URLs may be a posting whose
        # title scopes it to another country. If the region predicate
        # ever loosened enough to admit "Mexico City, Remote" or
        # "Buenos Aires, Argentina", this fails before the count does.
        payload = _load_fixture("varicent")
        by_url = {j["absolute_url"]: j for j in payload["jobs"]}
        with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{_API_BASE}/varicent/jobs").mock(
                return_value=httpx.Response(200, json=payload)
            )
            result = _run(
                GreenhouseStrategy().extract(_make_company("varicent"), _ctx())
            )

        for url in result["jobs"]:
            title = by_url[url]["title"]
            assert "Mexico Only" not in title
            assert "Argentina Only" not in title
            assert "costa rica" in by_url[url]["location"]["name"].lower()


# ---------------------------------------------------------------------------
# Retry + failure modes
# ---------------------------------------------------------------------------


class TestRetryAndFailures:
    """Retry policy and error-report contract."""

    def test_500_then_500_produces_error_report(self) -> None:
        with respx.mock() as mock:
            route = mock.get(f"{_API_BASE}/zscaler/jobs").mock(
                side_effect=[
                    httpx.Response(500, text="boom"),
                    httpx.Response(500, text="boom again"),
                ]
            )
            result = _run(
                GreenhouseStrategy().extract(_make_company("zscaler"), _ctx())
            )
            assert route.call_count == 2

        assert result["jobs"] == []
        assert result["metadata"]["error"] is not None
        assert "500" in result["metadata"]["error"]

    def test_500_then_200_retries_and_succeeds(self) -> None:
        payload = _load_fixture("zscaler")
        with respx.mock() as mock:
            route = mock.get(f"{_API_BASE}/zscaler/jobs").mock(
                side_effect=[
                    httpx.Response(500, text="transient"),
                    httpx.Response(200, json=payload),
                ]
            )
            result = _run(
                GreenhouseStrategy().extract(_make_company("zscaler"), _ctx())
            )
            assert route.call_count == 2

        assert result["metadata"]["error"] is None
        assert len(result["jobs"]) == 2

    def test_transport_error_then_200_retries_and_succeeds(self) -> None:
        # A DNS / connection / read-timeout failure counts against the
        # same one-retry budget as an HTTP 5xx.
        payload = _load_fixture("zscaler")
        with respx.mock() as mock:
            route = mock.get(f"{_API_BASE}/zscaler/jobs").mock(
                side_effect=[
                    httpx.ConnectError("simulated conn refused"),
                    httpx.Response(200, json=payload),
                ]
            )
            result = _run(
                GreenhouseStrategy().extract(_make_company("zscaler"), _ctx())
            )
            assert route.call_count == 2

        assert result["metadata"]["error"] is None
        assert len(result["jobs"]) == 2

    def test_transport_error_twice_produces_error_report(self) -> None:
        with respx.mock() as mock:
            route = mock.get(f"{_API_BASE}/zscaler/jobs").mock(
                side_effect=[
                    httpx.ConnectError("simulated 1"),
                    httpx.ConnectError("simulated 2"),
                ]
            )
            result = _run(
                GreenhouseStrategy().extract(_make_company("zscaler"), _ctx())
            )
            assert route.call_count == 2

        assert result["jobs"] == []
        assert result["metadata"]["error"] is not None
        assert "ConnectError" in result["metadata"]["error"]

    def test_missing_jobs_key_produces_error_report(self) -> None:
        with respx.mock() as mock:
            mock.get(f"{_API_BASE}/zscaler/jobs").mock(
                return_value=httpx.Response(200, json={"meta": {"total": 0}}),
            )
            result = _run(
                GreenhouseStrategy().extract(_make_company("zscaler"), _ctx())
            )

        assert result["jobs"] == []
        assert result["metadata"]["error"] is not None
        assert "jobs" in result["metadata"]["error"]

    def test_non_object_body_produces_error_report(self) -> None:
        with respx.mock() as mock:
            mock.get(f"{_API_BASE}/zscaler/jobs").mock(
                return_value=httpx.Response(200, json=[1, 2, 3]),
            )
            result = _run(
                GreenhouseStrategy().extract(_make_company("zscaler"), _ctx())
            )

        assert result["jobs"] == []
        assert result["metadata"]["error"] is not None

    def test_404_produces_error_report(self) -> None:
        # 404 is not a 5xx — no retry, immediate error report. Most
        # often means a wrong / retired board token.
        with respx.mock() as mock:
            route = mock.get(f"{_API_BASE}/badtoken/jobs").mock(
                return_value=httpx.Response(404, text="Not Found")
            )
            result = _run(
                GreenhouseStrategy().extract(_make_company("badtoken"), _ctx())
            )
            assert route.call_count == 1

        assert result["jobs"] == []
        assert result["metadata"]["error"] is not None
        assert "404" in result["metadata"]["error"]


# ---------------------------------------------------------------------------
# Region-filter edges (synthetic payloads)
# ---------------------------------------------------------------------------


class TestRegionFilterSynthetic:
    """Region-filter edge cases driven by synthetic (inline) payloads."""

    def test_compound_location_matches_costa_rica(self) -> None:
        # ``Remote - Costa Rica; Austin`` is the kind of compound
        # string some tenants use for multi-location postings. The
        # ``TargetRegion.matches`` predicate is regex-based, so a
        # match anywhere in the string counts.
        synthetic = {
            "jobs": [
                {
                    "id": 1,
                    "absolute_url": "https://example.test/jobs/1",
                    "location": {"name": "Remote - Costa Rica; Austin"},
                },
                {
                    "id": 2,
                    "absolute_url": "https://example.test/jobs/2",
                    "location": {"name": "Austin, TX"},
                },
            ],
            "meta": {"total": 2},
        }
        with respx.mock() as mock:
            mock.get(f"{_API_BASE}/synth/jobs").mock(
                return_value=httpx.Response(200, json=synthetic),
            )
            result = _run(GreenhouseStrategy().extract(_make_company("synth"), _ctx()))

        assert result["metadata"]["error"] is None
        assert result["jobs"] == ["https://example.test/jobs/1"]

    def test_zero_matches_is_honest_empty_not_error(self) -> None:
        # A 200 with no region-matching postings is a valid state:
        # the tenant simply doesn't hire in Costa Rica right now.
        # Distinguishable from ``metadata.error`` populated (broken
        # board) — see the module docstring's failure-semantics table.
        synthetic = {
            "jobs": [
                {
                    "id": 1,
                    "absolute_url": "https://example.test/jobs/1",
                    "location": {"name": "Berlin, Germany"},
                },
                {
                    "id": 2,
                    "absolute_url": "https://example.test/jobs/2",
                    "location": {"name": "Cracow, Poland"},
                },
            ],
            "meta": {"total": 2},
        }
        with respx.mock() as mock:
            mock.get(f"{_API_BASE}/synth/jobs").mock(
                return_value=httpx.Response(200, json=synthetic),
            )
            result = _run(GreenhouseStrategy().extract(_make_company("synth"), _ctx()))

        assert result["jobs"] == []
        assert result["metadata"]["error"] is None
        assert result["metadata"]["total_jobs_found"] == 0

    def test_missing_location_field_is_skipped_not_errored(self) -> None:
        # A malformed job entry with no ``location`` should not blow
        # up extraction — it should simply be skipped. The alternative
        # (raising) would trigger the catch-all, erroring out the
        # whole run for one bad row.
        synthetic = {
            "jobs": [
                {
                    "id": 1,
                    "absolute_url": "https://example.test/jobs/1",
                    # no ``location`` key at all
                },
                {
                    "id": 2,
                    "absolute_url": "https://example.test/jobs/2",
                    "location": {"name": "Costa Rica"},
                },
            ],
        }
        with respx.mock() as mock:
            mock.get(f"{_API_BASE}/synth/jobs").mock(
                return_value=httpx.Response(200, json=synthetic),
            )
            result = _run(GreenhouseStrategy().extract(_make_company("synth"), _ctx()))

        assert result["metadata"]["error"] is None
        assert result["jobs"] == ["https://example.test/jobs/2"]
