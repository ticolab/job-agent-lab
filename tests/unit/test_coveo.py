"""Unit tests for the Coveo adapter (SYS-18).

Two layers, following the phenom/talentbrew precedent:

- **Pure helpers** — :func:`build_search_body`, the city normaliser,
  and the region predicate are ordinary functions with no HTTP.
- **The adapter** — driven through ``respx`` so no network is touched.
  The happy path replays the recorded payload at
  ``tests/fixtures/api/coveo/ust.json`` plus a **synthetic** token
  fixture — the captured JWT must never land in git.

Fixture provenance is worth stating precisely, because it is *not* the
evidence capture. ``spike/evidence/ust_coveo_cr.json`` records the
**browser's** contract: ``totalCount: 20`` with only 10 ``results``,
because the widget asks for ``numberOfResults: 10``. The committed
fixture instead records the **adapter's** contract — captured live on
2026-08-04 through :func:`build_search_body` itself, so 20 records
against ``totalCount: 20``, terminating in a single call exactly as the
live board does. It is also markedly smaller (~36 KB vs ~129 KB)
because the trimmed ``fieldsToInclude`` returns three raw fields per
record rather than 24.

Two properties of that payload drive several tests below. Every record
has ``raw.jobid`` disagreeing with its ``clickUri`` tail — 20 of 20 —
which is what makes the verbatim-emission pin (the standing round-1
warning) testable against real data rather than a synthetic fixture.
And every record carries a **list-typed** ``raw.city``
(``["Heredia"]``), the shape the re-verification predicate must handle.

The wire contract is documented on
:func:`vacantes.extraction.ats.coveo.build_search_body`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from vacantes.domain.company import (
    Company,
    CoveoConfig,
    LinkRule,
    RuntimeHooks,
)
from vacantes.domain.region import COSTA_RICA_LATAM
from vacantes.extraction.ats import browser_token as bt
from vacantes.extraction.ats.browser_token import extract_token
from vacantes.extraction.ats.coveo import (
    CoveoStrategy,
    _city_entries,
    _record_matches_region,
    build_search_body,
    search_endpoint_url,
)
from vacantes.extraction.base import RunContext

_ORG = "ustglobalproduction4ggrtx7v"
_SEARCH_HUB = "prod-jobs-search-hub"
_TOKEN_URL = "https://www.ust.com/services/search"
_SEARCH_URL = f"https://{_ORG}.org.coveo.com/rest/search/v2"
_FIXTURES = Path(__file__).parent.parent / "fixtures" / "api" / "coveo"


def _config() -> CoveoConfig:
    return CoveoConfig(
        organization_id=_ORG, search_hub=_SEARCH_HUB, token_url=_TOKEN_URL
    )


def _load_ust_fixture() -> dict[str, Any]:
    with (_FIXTURES / "ust.json").open() as fh:
        payload: dict[str, Any] = json.load(fh)
    return payload


def _load_token_fixture() -> dict[str, Any]:
    with (_FIXTURES / "ust_token.json").open() as fh:
        payload: dict[str, Any] = json.load(fh)
    return payload


def _empty_page(total_count: int = 20) -> dict[str, Any]:
    """A well-shaped response whose ``results`` list is empty."""
    return {"totalCount": total_count, "results": []}


def _record(
    click_uri: str,
    *,
    country: object = "Costa Rica",
    city: object = None,
    jobid: object = "49999",
) -> dict[str, Any]:
    raw: dict[str, Any] = {"country": country, "jobid": jobid}
    if city is not None:
        raw["city"] = city
    return {"clickUri": click_uri, "raw": raw}


def _make_company(**overrides: Any) -> Company:
    """Build a UST-shaped ``strategy="coveo"`` company."""
    defaults: dict[str, Any] = {
        "name": "UST",
        "job_board_url": "https://www.ust.com/en/jobsearch",
        "sample_job_url": "https://www.ust.com/jobs/80708213",
        "strategy": "coveo",
        "coveo": _config(),
        "expected_jobs": 20,
    }
    defaults.update(overrides)
    return Company(**defaults)


def _ctx() -> RunContext:
    return RunContext(
        model="gpt-4.1-mini", headless=True, max_steps=20, region=COSTA_RICA_LATAM
    )


def _run(coro: Any) -> Any:
    """Drive a coroutine in a worker thread's fresh loop.

    Mirrors ``tests/unit/test_phenom._run`` / ``test_talentbrew._run``;
    see the former's docstring for why ``asyncio.run`` is unusable on
    the pytest main thread.
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


def _mock_token(mock: respx.MockRouter) -> None:
    mock.get(_TOKEN_URL).mock(
        return_value=httpx.Response(200, json=_load_token_fixture())
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestSearchEndpointUrl:
    """The search host is derived from ``organization_id``, not config."""

    def test_shape(self) -> None:
        assert search_endpoint_url(_ORG) == (
            f"https://{_ORG}.org.coveo.com/rest/search/v2?organizationId={_ORG}"
        )

    def test_organization_id_appears_as_host_label_and_query_param(self) -> None:
        # Both occurrences are load-bearing in the captured request, so
        # a refactor that drops either half must fail here.
        url = search_endpoint_url("acme42")
        assert url.startswith("https://acme42.org.coveo.com/")
        assert url.endswith("organizationId=acme42")


class TestBuildSearchBody:
    """Pin the trimmed wire contract from §4.8.3."""

    def test_matches_the_documented_template(self) -> None:
        body = build_search_body(_config(), "Costa Rica")
        assert body == {
            "q": "",
            "tab": "default",
            "locale": "en-US",
            "searchHub": _SEARCH_HUB,
            "sortCriteria": "relevancy",
            "firstResult": 0,
            "numberOfResults": 100,
            "fieldsToInclude": ["city", "country", "jobid"],
            "facets": [
                {
                    "facetId": "country",
                    "field": "country",
                    "type": "specific",
                    "currentValues": [{"value": "Costa Rica", "state": "selected"}],
                }
            ],
        }

    def test_no_advanced_query_expression(self) -> None:
        # §4.8.3 binds "no ``aq``" — filtering is facet-state-based and
        # the captured working request carries none. Adding one would
        # diverge from the contract known to work.
        assert "aq" not in build_search_body(_config(), "Costa Rica")

    def test_facet_value_is_selected_not_idle(self) -> None:
        # ``state: "idle"`` is what the browser echoes for the ~100
        # unselected sibling values; only ``"selected"`` filters.
        body = build_search_body(_config(), "Costa Rica")
        facet = body["facets"][0]
        assert facet["currentValues"] == [{"value": "Costa Rica", "state": "selected"}]

    def test_fields_to_include_covers_the_reverification_fields(self) -> None:
        # Coveo scopes ``raw.*`` to a default set when the key is
        # absent; the client-side predicate reads country and city, so
        # both must be requested or every record could silently drop.
        fields = build_search_body(_config(), "Costa Rica")["fieldsToInclude"]
        assert "country" in fields
        assert "city" in fields

    def test_first_result_is_threaded(self) -> None:
        assert build_search_body(_config(), "Costa Rica", 100)["firstResult"] == 100

    def test_page_size_is_one_hundred(self) -> None:
        assert build_search_body(_config(), "Costa Rica")["numberOfResults"] == 100

    def test_search_hub_comes_from_config(self) -> None:
        cfg = CoveoConfig(
            organization_id=_ORG, search_hub="other-hub", token_url=_TOKEN_URL
        )
        assert build_search_body(cfg, "Costa Rica")["searchHub"] == "other-hub"


class TestCityEntries:
    """``raw.city`` is list-typed on the recorded payload."""

    def test_list_of_strings(self) -> None:
        assert _city_entries(["Heredia", "San Jose"]) == ["Heredia", "San Jose"]

    def test_bare_string(self) -> None:
        # A single-valued Coveo index would serve a bare string; both
        # shapes are accepted rather than assuming the observed one.
        assert _city_entries("Heredia") == ["Heredia"]

    def test_none_yields_nothing(self) -> None:
        assert _city_entries(None) == []

    def test_empty_string_yields_nothing(self) -> None:
        assert _city_entries("") == []

    def test_non_string_entries_are_dropped(self) -> None:
        assert _city_entries(["Heredia", 42, None, "San Jose"]) == [
            "Heredia",
            "San Jose",
        ]

    def test_unexpected_type_yields_nothing_rather_than_raising(self) -> None:
        # A malformed location field must not take down a run whose
        # other records are fine.
        assert _city_entries({"name": "Heredia"}) == []
        assert _city_entries(42) == []


class TestRecordMatchesRegion:
    """Client-side re-verification, belt to the facet's braces."""

    def test_country_alone_matches(self) -> None:
        assert _record_matches_region(
            _record("u", country="Costa Rica"), COSTA_RICA_LATAM
        )

    def test_list_city_with_matching_country(self) -> None:
        rec = _record("u", country="Costa Rica", city=["Heredia"])
        assert _record_matches_region(rec, COSTA_RICA_LATAM)

    def test_empty_country_rescued_by_city_composition(self) -> None:
        # An index whose country field is blank but whose city is
        # unambiguously in-region still matches via composition.
        rec = _record("u", country="", city=["Costa Rica"])
        assert _record_matches_region(rec, COSTA_RICA_LATAM)

    def test_non_regional_record_is_dropped(self) -> None:
        rec = _record("u", country="India", city=["Bangalore"])
        assert not _record_matches_region(rec, COSTA_RICA_LATAM)

    def test_record_with_no_location_information_is_dropped(self) -> None:
        assert not _record_matches_region(
            {"clickUri": "u", "raw": {"jobid": "1"}}, COSTA_RICA_LATAM
        )

    def test_missing_raw_is_dropped(self) -> None:
        assert not _record_matches_region({"clickUri": "u"}, COSTA_RICA_LATAM)

    def test_non_dict_raw_is_dropped(self) -> None:
        assert not _record_matches_region(
            {"clickUri": "u", "raw": "Costa Rica"}, COSTA_RICA_LATAM
        )

    def test_multi_city_record_matches_on_any_entry(self) -> None:
        rec = _record("u", country="", city=["Bangalore", "Costa Rica"])
        assert _record_matches_region(rec, COSTA_RICA_LATAM)

    def test_recorded_fixture_records_all_match(self) -> None:
        # Every recorded record is a genuine CR posting returned under
        # the CR facet; if the predicate ever drops one, the adapter
        # would under-count against a live board.
        payload = _load_ust_fixture()
        assert all(
            _record_matches_region(r, COSTA_RICA_LATAM) for r in payload["results"]
        )


# ---------------------------------------------------------------------------
# Adapter — happy path
# ---------------------------------------------------------------------------


class TestExtractHappyPathRecorded:
    """Replay the recorded payload through the adapter."""

    def test_emits_the_recorded_click_uris(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            _mock_token(mock)
            mock.post(_SEARCH_URL).mock(
                return_value=httpx.Response(200, json=_load_ust_fixture())
            )
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        assert result["metadata"]["error"] is None
        assert len(result["jobs"]) == 20

    def test_every_url_is_a_ust_jobs_path(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            _mock_token(mock)
            mock.post(_SEARCH_URL).mock(
                return_value=httpx.Response(200, json=_load_ust_fixture())
            )
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        for url in result["jobs"]:
            assert url.startswith("https://www.ust.com/jobs/"), url

    def test_known_record_pins_a_full_url(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            _mock_token(mock)
            mock.post(_SEARCH_URL).mock(
                return_value=httpx.Response(200, json=_load_ust_fixture())
            )
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        assert "https://www.ust.com/jobs/80708213" in result["jobs"]

    def test_report_carries_none_agent_fields_and_strategy(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            _mock_token(mock)
            mock.post(_SEARCH_URL).mock(
                return_value=httpx.Response(200, json=_load_ust_fixture())
            )
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        meta = result["metadata"]
        assert meta["strategy"] == "coveo"
        assert meta["model"] is None
        assert meta["agent_steps"] is None
        assert meta["agent_completed"] is None
        assert meta["agent_had_errors"] is None
        assert meta["expected_jobs"] == 20
        assert "verdict" in meta


class TestClickUriVerbatimEmission:
    """The standing round-1 warning, pinned against real data.

    ``raw.jobid`` and the ``clickUri`` tail belong to different id
    schemes and disagree on **every** recorded record. Reconstructing a
    URL from ``raw.jobid`` would emit links that do not resolve, so the
    adapter must pass ``clickUri`` through untouched.
    """

    def test_recorded_payload_really_has_mismatched_ids(self) -> None:
        # Guard the guard: if a future re-capture happens to align the
        # two schemes, the pin below would silently stop testing
        # anything, so assert the precondition explicitly.
        payload = _load_ust_fixture()
        for record in payload["results"]:
            tail = record["clickUri"].rstrip("/").rsplit("/", 1)[-1]
            assert str(record["raw"]["jobid"]) != tail, record["clickUri"]

    def test_emits_click_uri_not_a_jobid_reconstruction(self) -> None:
        # The first recorded record: clickUri tail 80708213, jobid 48689.
        with respx.mock(assert_all_called=False) as mock:
            _mock_token(mock)
            mock.post(_SEARCH_URL).mock(
                return_value=httpx.Response(200, json=_load_ust_fixture())
            )
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        assert "https://www.ust.com/jobs/80708213" in result["jobs"]
        assert "https://www.ust.com/jobs/48689" not in result["jobs"]
        # No emitted URL may end in any record's ``raw.jobid``.
        jobids = {str(r["raw"]["jobid"]) for r in _load_ust_fixture()["results"]}
        for url in result["jobs"]:
            assert url.rsplit("/", 1)[-1] not in jobids, url

    def test_record_without_click_uri_is_skipped_not_synthesized(self) -> None:
        payload = {
            "totalCount": 2,
            "results": [
                {"raw": {"country": "Costa Rica", "jobid": "48689"}},
                _record("https://www.ust.com/jobs/111"),
            ],
        }
        with respx.mock(assert_all_called=False) as mock:
            _mock_token(mock)
            mock.post(_SEARCH_URL).mock(
                side_effect=[
                    httpx.Response(200, json=payload),
                    httpx.Response(200, json=_empty_page(2)),
                ]
            )
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        assert result["jobs"] == ["https://www.ust.com/jobs/111"]
        assert result["metadata"]["error"] is None

    def test_empty_click_uri_is_skipped(self) -> None:
        payload = {
            "totalCount": 1,
            "results": [_record("", country="Costa Rica")],
        }
        with respx.mock(assert_all_called=False) as mock:
            _mock_token(mock)
            mock.post(_SEARCH_URL).mock(
                side_effect=[
                    httpx.Response(200, json=payload),
                    httpx.Response(200, json=_empty_page(1)),
                ]
            )
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        assert result["jobs"] == []
        assert result["metadata"]["error"] is None


class TestRequestContract:
    """What actually goes on the wire."""

    def test_bearer_header_carries_the_minted_token(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            _mock_token(mock)
            route = mock.post(_SEARCH_URL).mock(
                return_value=httpx.Response(200, json=_load_ust_fixture())
            )
            _run(CoveoStrategy().extract(_make_company(), _ctx()))
        sent = route.calls[0].request
        assert sent.headers["authorization"] == "Bearer test-token-not-a-real-jwt"

    def test_mint_is_a_bare_get_without_the_cache_buster(self) -> None:
        # The browser appends ``?currentDate=<ms>``; the P1 notes pin it
        # as not consumed server-side, so sending it would imply a
        # contract that does not exist.
        with respx.mock(assert_all_called=False) as mock:
            token_route = mock.get(_TOKEN_URL).mock(
                return_value=httpx.Response(200, json=_load_token_fixture())
            )
            mock.post(_SEARCH_URL).mock(
                return_value=httpx.Response(200, json=_load_ust_fixture())
            )
            _run(CoveoStrategy().extract(_make_company(), _ctx()))
        request = token_route.calls[0].request
        assert request.method == "GET"
        assert request.url.query == b""

    def test_token_is_minted_once_per_run(self) -> None:
        # Two search pages, still one mint — the 24h TTL makes per-page
        # minting pure waste.
        first = _load_ust_fixture()
        with respx.mock(assert_all_called=False) as mock:
            token_route = mock.get(_TOKEN_URL).mock(
                return_value=httpx.Response(200, json=_load_token_fixture())
            )
            mock.post(_SEARCH_URL).mock(
                side_effect=[
                    httpx.Response(200, json=first),
                    httpx.Response(200, json=_empty_page()),
                ]
            )
            _run(CoveoStrategy().extract(_make_company(), _ctx()))
        assert token_route.call_count == 1

    def test_body_carries_the_facet_and_hub(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            _mock_token(mock)
            route = mock.post(_SEARCH_URL).mock(
                return_value=httpx.Response(200, json=_load_ust_fixture())
            )
            _run(CoveoStrategy().extract(_make_company(), _ctx()))
        body = json.loads(route.calls[0].request.content)
        assert body["searchHub"] == _SEARCH_HUB
        assert body["numberOfResults"] == 100
        assert body["facets"][0]["currentValues"][0] == {
            "value": "Costa Rica",
            "state": "selected",
        }
        assert "aq" not in body

    def test_facet_value_is_the_regions_canonical_country_name(self) -> None:
        # ``filter_tokens[0]``, deliberately not the whole preferred
        # slice — which also holds "CR", a DOM-filter token the facet
        # has no value for.
        with respx.mock(assert_all_called=False) as mock:
            _mock_token(mock)
            route = mock.post(_SEARCH_URL).mock(
                return_value=httpx.Response(200, json=_load_ust_fixture())
            )
            _run(CoveoStrategy().extract(_make_company(), _ctx()))
        body = json.loads(route.calls[0].request.content)
        assert (
            body["facets"][0]["currentValues"][0]["value"]
            == (COSTA_RICA_LATAM.filter_tokens[0])
        )


# ---------------------------------------------------------------------------
# Failure semantics
# ---------------------------------------------------------------------------


class TestTokenMintFailures:
    """A run that cannot authenticate has nothing honest to report."""

    def test_transport_error_retries_then_succeeds(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_TOKEN_URL).mock(
                side_effect=[
                    httpx.ConnectError("boom"),
                    httpx.Response(200, json=_load_token_fixture()),
                ]
            )
            mock.post(_SEARCH_URL).mock(
                return_value=httpx.Response(200, json=_load_ust_fixture())
            )
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        assert result["metadata"]["error"] is None
        assert len(result["jobs"]) == 20

    def test_two_transport_errors_produce_an_error_report(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_TOKEN_URL).mock(
                side_effect=[httpx.ConnectError("a"), httpx.ConnectError("b")]
            )
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        assert result["metadata"]["error"] is not None
        assert result["jobs"] == []

    def test_server_error_retries_then_reports(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_TOKEN_URL).mock(
                side_effect=[httpx.Response(503), httpx.Response(503)]
            )
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        assert result["metadata"]["error"] is not None
        assert result["jobs"] == []

    def test_no_search_call_is_made_when_the_mint_fails(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_TOKEN_URL).mock(return_value=httpx.Response(500))
            search = mock.post(_SEARCH_URL).mock(
                return_value=httpx.Response(200, json=_load_ust_fixture())
            )
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        assert search.call_count == 0
        assert result["metadata"]["error"] is not None

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"token": ""},
            {"token": None},
            {"token": 12345},
            {"access_token": "wrong-key"},
        ],
        ids=["empty", "blank", "null", "non-string", "wrong-key"],
    )
    def test_malformed_token_body_is_a_loud_error(self, body: object) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_TOKEN_URL).mock(return_value=httpx.Response(200, json=body))
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        assert result["metadata"]["error"] is not None
        assert result["jobs"] == []

    def test_non_object_token_body_is_a_loud_error(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_TOKEN_URL).mock(
                return_value=httpx.Response(200, json=["not", "an", "object"])
            )
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        assert result["metadata"]["error"] is not None


class TestSearchFailures:
    """Search-side error semantics, including the v1 401 posture."""

    def test_401_is_a_hard_error_with_no_remint(self) -> None:
        # v1 mints once; the 24h TTL makes mid-run expiry impossible,
        # so a 401 is a real failure rather than a renewal trigger.
        with respx.mock(assert_all_called=False) as mock:
            token_route = mock.get(_TOKEN_URL).mock(
                return_value=httpx.Response(200, json=_load_token_fixture())
            )
            mock.post(_SEARCH_URL).mock(return_value=httpx.Response(401))
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        assert result["metadata"]["error"] is not None
        assert result["jobs"] == []
        assert token_route.call_count == 1

    def test_403_is_a_hard_error(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            _mock_token(mock)
            mock.post(_SEARCH_URL).mock(return_value=httpx.Response(403))
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        assert result["metadata"]["error"] is not None

    def test_server_error_retries_then_succeeds(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            _mock_token(mock)
            mock.post(_SEARCH_URL).mock(
                side_effect=[
                    httpx.Response(502),
                    httpx.Response(200, json=_load_ust_fixture()),
                    httpx.Response(200, json=_empty_page()),
                ]
            )
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        assert result["metadata"]["error"] is None
        assert len(result["jobs"]) == 20

    def test_two_server_errors_produce_an_error_report(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            _mock_token(mock)
            mock.post(_SEARCH_URL).mock(
                side_effect=[httpx.Response(500), httpx.Response(500)]
            )
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        assert result["metadata"]["error"] is not None
        assert result["jobs"] == []

    def test_transport_error_retries_then_reports(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            _mock_token(mock)
            mock.post(_SEARCH_URL).mock(
                side_effect=[httpx.ConnectError("a"), httpx.ConnectError("b")]
            )
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        assert result["metadata"]["error"] is not None

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"totalCount": 20},
            {"results": "not-a-list"},
            {"results": {"0": "dict-not-list"}},
        ],
        ids=["empty", "no-results-key", "results-string", "results-dict"],
    )
    def test_malformed_search_body_is_a_loud_error(self, body: object) -> None:
        # A contract breakage must never read as an empty board.
        with respx.mock(assert_all_called=False) as mock:
            _mock_token(mock)
            mock.post(_SEARCH_URL).mock(return_value=httpx.Response(200, json=body))
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        assert result["metadata"]["error"] is not None
        assert result["jobs"] == []

    def test_non_object_search_body_is_a_loud_error(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            _mock_token(mock)
            mock.post(_SEARCH_URL).mock(
                return_value=httpx.Response(200, json=[1, 2, 3])
            )
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        assert result["metadata"]["error"] is not None


class TestConfigFaultsAreReportedNotRaised:
    """Schema faults surface as reports, mirroring the siblings."""

    def test_missing_config_is_an_error_report(self) -> None:
        # The schema validator forbids this combination, so reach it the
        # way a hand-mutated Company would: construct a valid dom entry
        # and drive the coveo strategy against it directly.
        company = Company(
            name="No Config",
            job_board_url="https://www.ust.com/en/jobsearch",
            sample_job_url="https://www.ust.com/jobs/1",
        )
        with respx.mock(assert_all_called=False):
            result = _run(CoveoStrategy().extract(company, _ctx()))
        assert result["metadata"]["error"] is not None
        assert "CoveoConfig" in result["metadata"]["error"]
        assert result["jobs"] == []


class TestHonestEmpty:
    """Zero region matches is a result, not a failure."""

    def test_empty_result_list_is_not_an_error(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            _mock_token(mock)
            mock.post(_SEARCH_URL).mock(
                return_value=httpx.Response(200, json={"totalCount": 0, "results": []})
            )
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        assert result["metadata"]["error"] is None
        assert result["jobs"] == []

    def test_all_records_out_of_region_is_not_an_error(self) -> None:
        payload = {
            "totalCount": 2,
            "results": [
                _record("https://www.ust.com/jobs/1", country="India", city=["Kochi"]),
                _record(
                    "https://www.ust.com/jobs/2", country="Poland", city=["Gdansk"]
                ),
            ],
        }
        with respx.mock(assert_all_called=False) as mock:
            _mock_token(mock)
            mock.post(_SEARCH_URL).mock(
                side_effect=[
                    httpx.Response(200, json=payload),
                    httpx.Response(200, json=_empty_page(2)),
                ]
            )
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        assert result["metadata"]["error"] is None
        assert result["jobs"] == []


class TestClientSideReverification:
    """The belt catches what the facet's braces let through."""

    def test_injected_non_cr_record_is_dropped(self) -> None:
        payload = _load_ust_fixture()
        payload = dict(payload)
        payload["results"] = [
            *payload["results"],
            _record(
                "https://www.ust.com/jobs/999999",
                country="India",
                city=["Bangalore"],
            ),
        ]
        with respx.mock(assert_all_called=False) as mock:
            _mock_token(mock)
            mock.post(_SEARCH_URL).mock(
                side_effect=[
                    httpx.Response(200, json=payload),
                    httpx.Response(200, json=_empty_page()),
                ]
            )
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        assert "https://www.ust.com/jobs/999999" not in result["jobs"]
        assert len(result["jobs"]) == 20

    def test_list_city_record_is_kept(self) -> None:
        payload = {
            "totalCount": 1,
            "results": [
                _record(
                    "https://www.ust.com/jobs/555",
                    country="Costa Rica",
                    city=["Heredia"],
                )
            ],
        }
        with respx.mock(assert_all_called=False) as mock:
            _mock_token(mock)
            mock.post(_SEARCH_URL).mock(
                side_effect=[
                    httpx.Response(200, json=payload),
                    httpx.Response(200, json=_empty_page(1)),
                ]
            )
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        assert result["jobs"] == ["https://www.ust.com/jobs/555"]


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class TestPagination:
    """``firstResult`` paging — specified, and exercised here by test."""

    def _page(self, ids: list[int], total: int) -> dict[str, Any]:
        return {
            "totalCount": total,
            "results": [
                _record(f"https://www.ust.com/jobs/{i}", city=["Heredia"], jobid=str(i))
                for i in ids
            ],
        }

    def test_two_pages_are_unioned(self) -> None:
        # ``totalCount`` exceeds page 1's record count, so the loop
        # advances; page 2 completes the set and terminates it.
        with respx.mock(assert_all_called=False) as mock:
            _mock_token(mock)
            mock.post(_SEARCH_URL).mock(
                side_effect=[
                    httpx.Response(200, json=self._page([1, 2], 3)),
                    httpx.Response(200, json=self._page([3], 3)),
                ]
            )
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        assert result["jobs"] == [
            "https://www.ust.com/jobs/1",
            "https://www.ust.com/jobs/2",
            "https://www.ust.com/jobs/3",
        ]

    def test_first_result_advances_by_records_seen(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            _mock_token(mock)
            route = mock.post(_SEARCH_URL).mock(
                side_effect=[
                    httpx.Response(200, json=self._page([1, 2], 3)),
                    httpx.Response(200, json=self._page([3], 3)),
                ]
            )
            _run(CoveoStrategy().extract(_make_company(), _ctx()))
        assert json.loads(route.calls[0].request.content)["firstResult"] == 0
        assert json.loads(route.calls[1].request.content)["firstResult"] == 2

    def test_single_page_when_total_is_satisfied(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            _mock_token(mock)
            route = mock.post(_SEARCH_URL).mock(
                return_value=httpx.Response(200, json=self._page([1, 2], 2))
            )
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        assert route.call_count == 1
        assert len(result["jobs"]) == 2

    def test_empty_page_terminates(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            _mock_token(mock)
            route = mock.post(_SEARCH_URL).mock(
                side_effect=[
                    httpx.Response(200, json=self._page([1], 99)),
                    httpx.Response(200, json=_empty_page(99)),
                ]
            )
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        # Stops at the empty page rather than looping to _MAX_PAGES.
        assert route.call_count == 2
        assert result["jobs"] == ["https://www.ust.com/jobs/1"]

    def test_duplicate_click_uris_across_pages_dedupe_in_order(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            _mock_token(mock)
            mock.post(_SEARCH_URL).mock(
                side_effect=[
                    httpx.Response(200, json=self._page([1, 2], 4)),
                    httpx.Response(200, json=self._page([2, 3], 4)),
                ]
            )
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        assert result["jobs"] == [
            "https://www.ust.com/jobs/1",
            "https://www.ust.com/jobs/2",
            "https://www.ust.com/jobs/3",
        ]

    def test_max_pages_cap_warns_and_emits_the_subset(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A totalCount the loop can never satisfy: each page returns one
        # record against a total of 10_000.
        pages = [
            httpx.Response(200, json=self._page([i], 10_000)) for i in range(1, 40)
        ]
        with (
            caplog.at_level(logging.WARNING),
            respx.mock(assert_all_called=False) as mock,
        ):
            _mock_token(mock)
            route = mock.post(_SEARCH_URL).mock(side_effect=pages)
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        # One record per page × the cap — not the fixture's 20.
        assert route.call_count == 10  # _MAX_PAGES
        assert len(result["jobs"]) == 10
        assert result["metadata"]["error"] is None
        assert any("_MAX_PAGES" in r.getMessage() for r in caplog.records)


class TestUnderFetchWarning:
    """Ending short of ``totalCount`` is a warning, not an error.

    The committed fixture is the adapter's *own* contract (20 records
    against ``totalCount: 20``), so it no longer exercises this path —
    the shortfall shape is synthesized here instead. The live board
    reaches its full set in one call; this test exists for the tenant
    whose server reports more than it serves.
    """

    def test_warns_when_total_count_exceeds_retrieved_records(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A server claiming 20 but serving 3, then nothing: the loop
        # advances once, gets an empty page, and terminates short.
        short_page = {
            "totalCount": 20,
            "results": [
                _record(f"https://www.ust.com/jobs/{i}", city=["Heredia"])
                for i in (1, 2, 3)
            ],
        }
        with (
            caplog.at_level(logging.WARNING),
            respx.mock(assert_all_called=False) as mock,
        ):
            _mock_token(mock)
            mock.post(_SEARCH_URL).mock(
                side_effect=[
                    httpx.Response(200, json=short_page),
                    httpx.Response(200, json=_empty_page()),
                ]
            )
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))
        assert len(result["jobs"]) == 3
        assert result["metadata"]["error"] is None
        assert result["metadata"]["verdict"] == "under"
        assert any("totalCount=20" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# SYS-19: the browser-borrowed token source.
# ---------------------------------------------------------------------------

_BROWSER_KEY = "searchToken_en_us"
_BORROWED = "eyJhbGciOiJIUzI1NiJ9.borrowed.signature"


def _browser_config() -> CoveoConfig:
    """UST's shipped shape: browser token source, no ``token_url``."""
    return CoveoConfig(
        organization_id=_ORG,
        search_hub=_SEARCH_HUB,
        browser_token_key=_BROWSER_KEY,
    )


def _make_browser_company(**overrides: Any) -> Company:
    company_overrides: dict[str, Any] = {"coveo": _browser_config()}
    company_overrides.update(overrides)
    return _make_company(**company_overrides)


class TestExtractToken:
    """The pure unwrap rule, testable without a browser."""

    def test_bare_jwt_passes_through(self) -> None:
        assert extract_token(_BORROWED) == _BORROWED

    def test_surrounding_whitespace_stripped(self) -> None:
        assert extract_token(f"  {_BORROWED}\n") == _BORROWED

    def test_envelope_is_unwrapped(self) -> None:
        # UST stores the bare JWT, but the mint *response* it came from
        # is this envelope, so a tenant persisting the whole thing is a
        # plausible variant rather than a hypothetical.
        assert extract_token(json.dumps({"token": _BORROWED})) == _BORROWED

    def test_envelope_without_token_raises(self) -> None:
        # Returning the blob verbatim would send JSON as a bearer
        # credential and fail later, further from the cause.
        with pytest.raises(ValueError, match="no non-empty string 'token'"):
            extract_token(json.dumps({"jwt": _BORROWED}))

    def test_envelope_with_empty_token_raises(self) -> None:
        with pytest.raises(ValueError, match="no non-empty string 'token'"):
            extract_token(json.dumps({"token": ""}))

    def test_non_json_starting_with_brace_is_opaque(self) -> None:
        # Do not second-guess a tenant whose token merely starts with "{".
        assert extract_token("{not-json") == "{not-json"

    def test_json_non_object_is_opaque(self) -> None:
        assert extract_token('{"a"') == '{"a"'


class TestBrowserTokenSourceSelection:
    """Which source the adapter picks, and what it does with the token."""

    def test_borrowed_token_is_sent_as_bearer(self, monkeypatch: Any) -> None:
        seen: dict[str, Any] = {}

        async def _fake(url: str, key: str, *, headless: bool) -> str:
            seen.update(url=url, key=key, headless=headless)
            return _BORROWED

        monkeypatch.setattr(bt, "read_session_storage_token", _fake)
        with respx.mock(assert_all_called=False) as mock:
            route = mock.post(_SEARCH_URL).mock(
                return_value=httpx.Response(200, json=_load_ust_fixture())
            )
            result = _run(CoveoStrategy().extract(_make_browser_company(), _ctx()))

        assert result["metadata"]["error"] is None
        assert route.calls[0].request.headers["authorization"] == f"Bearer {_BORROWED}"
        # Helper receives the board URL — the page that mints — and the
        # configured key, at the run's headless setting.
        assert seen == {
            "url": "https://www.ust.com/en/jobsearch",
            "key": _BROWSER_KEY,
            "headless": True,
        }

    def test_token_url_is_never_requested_on_the_browser_path(
        self, monkeypatch: Any
    ) -> None:
        # The whole point of C19: the gated mint endpoint must not be
        # touched. A route that is never called proves it.
        async def _fake(url: str, key: str, *, headless: bool) -> str:
            return _BORROWED

        monkeypatch.setattr(bt, "read_session_storage_token", _fake)
        with respx.mock(assert_all_called=False) as mock:
            token_route = mock.get(_TOKEN_URL).mock(
                return_value=httpx.Response(200, json={"token": "should-not-be-used"})
            )
            mock.post(_SEARCH_URL).mock(
                return_value=httpx.Response(200, json=_load_ust_fixture())
            )
            _run(CoveoStrategy().extract(_make_browser_company(), _ctx()))

        assert token_route.call_count == 0

    def test_helper_not_called_on_the_httpx_path(self, monkeypatch: Any) -> None:
        called = False

        async def _fake(url: str, key: str, *, headless: bool) -> str:
            nonlocal called
            called = True
            return _BORROWED

        monkeypatch.setattr(bt, "read_session_storage_token", _fake)
        with respx.mock(assert_all_called=False) as mock:
            _mock_token(mock)
            mock.post(_SEARCH_URL).mock(
                return_value=httpx.Response(200, json=_load_ust_fixture())
            )
            result = _run(CoveoStrategy().extract(_make_company(), _ctx()))

        assert called is False
        assert result["metadata"]["error"] is None

    def test_headless_false_is_threaded_through(self, monkeypatch: Any) -> None:
        seen: dict[str, Any] = {}

        async def _fake(url: str, key: str, *, headless: bool) -> str:
            seen["headless"] = headless
            return _BORROWED

        monkeypatch.setattr(bt, "read_session_storage_token", _fake)
        ctx = RunContext(
            model="unused", headless=False, max_steps=1, region=COSTA_RICA_LATAM
        )
        with respx.mock(assert_all_called=False) as mock:
            mock.post(_SEARCH_URL).mock(
                return_value=httpx.Response(200, json=_load_ust_fixture())
            )
            _run(CoveoStrategy().extract(_make_browser_company(), ctx))

        assert seen["headless"] is False


class TestBrowserTokenFailures:
    """A borrow failure is an honest error report, never a crash."""

    def test_helper_failure_folds_into_error_report(self, monkeypatch: Any) -> None:
        async def _boom(url: str, key: str, *, headless: bool) -> str:
            raise ValueError("sessionStorage['searchToken_en_us'] was never populated")

        monkeypatch.setattr(bt, "read_session_storage_token", _boom)
        with respx.mock(assert_all_called=False) as mock:
            mock.post(_SEARCH_URL).mock(
                return_value=httpx.Response(200, json=_load_ust_fixture())
            )
            result = _run(CoveoStrategy().extract(_make_browser_company(), _ctx()))

        assert result["jobs"] == []
        assert "never populated" in (result["metadata"]["error"] or "")

    def test_search_is_not_attempted_when_the_borrow_fails(
        self, monkeypatch: Any
    ) -> None:
        async def _boom(url: str, key: str, *, headless: bool) -> str:
            raise ValueError("gate challenged the load")

        monkeypatch.setattr(bt, "read_session_storage_token", _boom)
        with respx.mock(assert_all_called=False) as mock:
            search = mock.post(_SEARCH_URL).mock(
                return_value=httpx.Response(200, json=_load_ust_fixture())
            )
            _run(CoveoStrategy().extract(_make_browser_company(), _ctx()))

        assert search.call_count == 0

    def test_neither_source_set_is_caught_by_the_extract_time_belt(self) -> None:
        # model_construct bypasses the schema validator, standing in for
        # a hand-mutated config — the same belt greenhouse.board_token has.
        bad = CoveoConfig.model_construct(
            organization_id=_ORG,
            search_hub=_SEARCH_HUB,
            token_url=None,
            browser_token_key=None,
        )
        company = Company.model_construct(
            name="UST",
            job_board_url="https://www.ust.com/en/jobsearch",
            sample_job_url="https://www.ust.com/jobs/80708213",
            strategy="coveo",
            coveo=bad,
            link_rule=LinkRule(),
            aliases=(),
            paginate=False,
            expected_jobs=20,
            hooks=RuntimeHooks(),
            pre_filter_urls=(),
            phenom=None,
            talentbrew=None,
        )
        result = _run(CoveoStrategy().extract(company, _ctx()))
        assert result["jobs"] == []
        assert "neither token_url nor browser_token_key" in (
            result["metadata"]["error"] or ""
        )


class TestBrowserPathRecordedPayload:
    """The shipped UST shape end-to-end, browser half mocked at the seam."""

    def test_yields_twenty_urls(self, monkeypatch: Any) -> None:
        async def _fake(url: str, key: str, *, headless: bool) -> str:
            return _BORROWED

        monkeypatch.setattr(bt, "read_session_storage_token", _fake)
        with respx.mock(assert_all_called=False) as mock:
            mock.post(_SEARCH_URL).mock(
                return_value=httpx.Response(200, json=_load_ust_fixture())
            )
            result = _run(CoveoStrategy().extract(_make_browser_company(), _ctx()))

        assert len(result["jobs"]) == 20
        assert result["metadata"]["verdict"] == "match"

    def test_click_uri_emitted_verbatim(self, monkeypatch: Any) -> None:
        # SYS-18's sharpest rule, re-pinned on the new token path:
        # raw.jobid disagrees with the clickUri tail on every record, so
        # a reconstructed URL would not resolve.
        async def _fake(url: str, key: str, *, headless: bool) -> str:
            return _BORROWED

        monkeypatch.setattr(bt, "read_session_storage_token", _fake)
        with respx.mock(assert_all_called=False) as mock:
            mock.post(_SEARCH_URL).mock(
                return_value=httpx.Response(200, json=_load_ust_fixture())
            )
            result = _run(CoveoStrategy().extract(_make_browser_company(), _ctx()))

        expected = [
            r["clickUri"]
            for r in _load_ust_fixture()["results"]
            if _record_matches_region(r, COSTA_RICA_LATAM)
        ]
        assert result["jobs"] == expected
