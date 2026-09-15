"""Unit tests for the BambooHR careers-list API strategy.

Three layers of coverage, mirroring ``test_greenhouse.py``:

- ``board_origin`` — the extract-time belt to the ``Company`` schema
  validator's braces: happy path plus each raise-worthy edge
  (non-bamboohr strategy, foreign host).

- ``location_text`` — the field-precedence rule. This is the piece
  carrying the adapter's whole reason for existing, so it is tested
  directly rather than only through the strategy: ``atsLocation``
  (country-bearing) wins, ``location`` (city/state only) is the
  fallback, and a record with neither collapses to ``""``.

- ``BambooHrStrategy.extract`` — the full runtime through ``respx`` so
  no network is touched, replaying the live-recorded payload at
  ``tests/fixtures/api/bamboohr/cornelisnetworks.json``.

The San Jose case gets dedicated tests. Cornelis' board carries both
``San Jose / Costa Rica`` and ``San Jose / California / United States``
postings, so a city-only comparison would silently conflate them —
the same ambiguity Veeam's Talentbrew board exhibits. These tests pin
that the country field is what separates them.

Async coverage uses the worker-thread ``_run`` helper for the same
reason documented in ``test_greenhouse._run``: ``pytest-playwright``
leaves a running-loop thread-local on the pytest main thread that makes
``asyncio.run`` unusable there.
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
from pydantic import ValidationError

from vacantes.domain.company import Company
from vacantes.domain.region import COSTA_RICA_LATAM
from vacantes.extraction.ats.bamboohr import (
    BambooHrStrategy,
    board_origin,
    location_text,
    select_region_urls,
)
from vacantes.extraction.base import RunContext

_ORIGIN = "https://cornelisnetworks.bamboohr.com"
_ENDPOINT = f"{_ORIGIN}/careers/list"

_FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "api" / "bamboohr"

# The seven Costa Rica postings on the recorded board, by id. Pinned
# explicitly rather than counted so a fixture re-record that changes
# *which* postings match fails loudly instead of silently staying at
# the same total.
_EXPECTED_CR_IDS = ("236", "248", "257", "258", "259", "263", "264")


def _load_fixture() -> dict[str, Any]:
    """Read the recorded Cornelis ``/careers/list`` payload."""
    payload = json.loads((_FIXTURE_DIR / "cornelisnetworks.json").read_text())
    assert isinstance(payload, dict), "cornelisnetworks.json is not a JSON object"
    return payload


def _make_company(**overrides: Any) -> Company:
    """Build the shipped ``Cornelis Networks`` catalog entry's shape."""
    defaults: dict[str, Any] = {
        "name": "Cornelis Networks",
        "job_board_url": f"{_ORIGIN}/careers",
        "sample_job_url": f"{_ORIGIN}/careers/236",
        "strategy": "bamboohr",
        "expected_jobs": 7,
    }
    defaults.update(overrides)
    return Company(**defaults)


def _ctx() -> RunContext:
    """Build a ``RunContext``; the DOM-only knobs are sentinels."""
    return RunContext(
        model="unused-model",
        headless=True,
        max_steps=1,
        region=COSTA_RICA_LATAM,
    )


def _run(coro: Any) -> Any:
    """Drive a coroutine in a worker thread's fresh loop.

    See ``test_greenhouse._run`` for why the pytest main thread cannot
    host the loop.
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


class TestBoardOrigin:
    """The extract-time host belt."""

    def test_returns_scheme_and_host(self) -> None:
        assert board_origin(_make_company()) == _ORIGIN

    def test_ignores_path_and_query(self) -> None:
        c = _make_company(job_board_url=f"{_ORIGIN}/careers?dept=eng")
        assert board_origin(c) == _ORIGIN

    def test_rejects_non_bamboohr_strategy(self) -> None:
        c = Company(
            name="X",
            job_board_url=f"{_ORIGIN}/careers",
            sample_job_url=f"{_ORIGIN}/careers/1",
        )
        with pytest.raises(ValueError, match="expects strategy='bamboohr'"):
            board_origin(c)

    def test_rejects_foreign_host_on_hand_mutated_company(self) -> None:
        # The schema validator normally makes this unreachable; build
        # the object via model_construct to bypass validation and prove
        # the belt fires independently.
        c = Company.model_construct(
            name="X",
            job_board_url="https://evil.example.com/careers",
            sample_job_url="https://evil.example.com/careers/1",
            strategy="bamboohr",
        )
        with pytest.raises(ValueError, match="tenant subdomain"):
            board_origin(c)


class TestHostValidator:
    """The schema-time gate on ``strategy="bamboohr"``."""

    def test_tenant_subdomain_accepted(self) -> None:
        assert _make_company().strategy == "bamboohr"

    def test_marketing_site_rejected(self) -> None:
        with pytest.raises(ValidationError, match="tenant subdomain"):
            _make_company(job_board_url="https://www.cornelis.com/company/careers")

    def test_bare_platform_domain_rejected(self) -> None:
        with pytest.raises(ValidationError, match="tenant subdomain"):
            _make_company(job_board_url="https://bamboohr.com/careers")

    def test_dom_strategy_on_bamboohr_host_is_untouched(self) -> None:
        # Gorilla Logic and Chainstack sit on BambooHR hosts while
        # running the DOM strategy. The validator must not reach them.
        c = Company(
            name="Gorilla Logic",
            job_board_url="https://gorillalogic.bamboohr.com/careers",
            sample_job_url="https://gorillalogic.bamboohr.com/careers/182",
        )
        assert c.strategy == "dom"


class TestLocationText:
    """Field precedence between the two location objects."""

    def test_ats_location_preferred_and_country_included(self) -> None:
        rec = {
            "atsLocation": {
                "city": "San Jose",
                "state": None,
                "province": None,
                "country": "Costa Rica",
            },
            "location": {"city": "Ignored", "state": "Ignored"},
        }
        assert location_text(rec) == "San Jose, Costa Rica"

    def test_field_order_is_city_state_province_country(self) -> None:
        rec = {
            "atsLocation": {
                "city": "C",
                "state": "S",
                "province": "P",
                "country": "K",
            }
        }
        assert location_text(rec) == "C, S, P, K"

    def test_falls_back_to_location_when_ats_empty(self) -> None:
        rec = {
            "atsLocation": {
                "city": None,
                "state": None,
                "province": None,
                "country": None,
            },
            "location": {"city": "Wayne", "state": "Pennsylvania"},
        }
        assert location_text(rec) == "Wayne, Pennsylvania"

    def test_missing_objects_collapse_to_empty_string(self) -> None:
        assert location_text({}) == ""

    def test_null_objects_collapse_to_empty_string(self) -> None:
        assert location_text({"atsLocation": None, "location": None}) == ""

    def test_empty_string_is_not_region_matched(self) -> None:
        # The predicate must reject "" rather than raise — a posting
        # with no stated location cannot be shown to be in-region.
        assert COSTA_RICA_LATAM.matches(location_text({})) is False


class TestSelectRegionUrls:
    """URL synthesis and record-level robustness."""

    def test_synthesises_posting_url_from_id(self) -> None:
        recs = [{"id": "236", "atsLocation": {"country": "Costa Rica"}}]
        assert select_region_urls(recs, _ORIGIN, COSTA_RICA_LATAM) == [
            f"{_ORIGIN}/careers/236"
        ]

    def test_non_matching_records_dropped(self) -> None:
        recs = [{"id": "1", "atsLocation": {"country": "United States"}}]
        assert select_region_urls(recs, _ORIGIN, COSTA_RICA_LATAM) == []

    def test_integer_ids_accepted(self) -> None:
        recs = [{"id": 236, "atsLocation": {"country": "Costa Rica"}}]
        assert select_region_urls(recs, _ORIGIN, COSTA_RICA_LATAM) == [
            f"{_ORIGIN}/careers/236"
        ]

    def test_malformed_rows_skipped_not_raised(self) -> None:
        recs: list[Any] = [
            "not-a-dict",
            {"atsLocation": {"country": "Costa Rica"}},  # no id
            {"id": None, "atsLocation": {"country": "Costa Rica"}},
            {"id": "  ", "atsLocation": {"country": "Costa Rica"}},
            {"id": "9", "atsLocation": {"country": "Costa Rica"}},
        ]
        assert select_region_urls(recs, _ORIGIN, COSTA_RICA_LATAM) == [
            f"{_ORIGIN}/careers/9"
        ]

    def test_duplicate_ids_deduped(self) -> None:
        recs = [
            {"id": "5", "atsLocation": {"country": "Costa Rica"}},
            {"id": "5", "atsLocation": {"country": "Costa Rica"}},
        ]
        assert select_region_urls(recs, _ORIGIN, COSTA_RICA_LATAM) == [
            f"{_ORIGIN}/careers/5"
        ]

    def test_payload_order_preserved(self) -> None:
        recs = [
            {"id": "9", "atsLocation": {"country": "Costa Rica"}},
            {"id": "2", "atsLocation": {"country": "Costa Rica"}},
        ]
        assert select_region_urls(recs, _ORIGIN, COSTA_RICA_LATAM) == [
            f"{_ORIGIN}/careers/9",
            f"{_ORIGIN}/careers/2",
        ]


class TestSanJoseAmbiguity:
    """City alone cannot decide the region on this board."""

    def test_san_jose_costa_rica_matches(self) -> None:
        rec = {"atsLocation": {"city": "San Jose", "country": "Costa Rica"}}
        assert COSTA_RICA_LATAM.matches(location_text(rec)) is True

    def test_san_jose_california_does_not_match(self) -> None:
        rec = {
            "atsLocation": {
                "city": "San Jose",
                "state": "California",
                "country": "United States",
            }
        }
        assert COSTA_RICA_LATAM.matches(location_text(rec)) is False

    def test_both_present_only_costa_rica_selected(self) -> None:
        recs = [
            {
                "id": "cr",
                "atsLocation": {"city": "San Jose", "country": "Costa Rica"},
            },
            {
                "id": "us",
                "atsLocation": {
                    "city": "San Jose",
                    "state": "California",
                    "country": "United States",
                },
            },
        ]
        assert select_region_urls(recs, _ORIGIN, COSTA_RICA_LATAM) == [
            f"{_ORIGIN}/careers/cr"
        ]


class TestRecordedPayload:
    """The live-recorded Cornelis board, replayed through respx."""

    def test_yields_seven_urls(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_load_fixture())
            )
            result = _run(BambooHrStrategy().extract(_make_company(), _ctx()))
        assert result["metadata"]["error"] is None
        assert len(result["jobs"]) == 7

    def test_pins_the_exact_seven_ids(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_load_fixture())
            )
            result = _run(BambooHrStrategy().extract(_make_company(), _ctx()))
        assert result["jobs"] == [f"{_ORIGIN}/careers/{i}" for i in _EXPECTED_CR_IDS]

    def test_board_is_larger_than_the_region_set(self) -> None:
        # The whole point of the adapter: the DOM path would return all
        # 32. If a future fixture re-record makes these equal, the
        # adapter has stopped earning its keep on this board.
        payload = _load_fixture()
        assert len(payload["result"]) == 32

    def test_sample_job_url_is_among_the_results(self) -> None:
        # The catalog entry's sample_job_url must be a real in-region
        # posting, not an arbitrary one.
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_load_fixture())
            )
            result = _run(BambooHrStrategy().extract(_make_company(), _ctx()))
        assert _make_company().sample_job_url in result["jobs"]

    def test_agent_fields_are_none(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_load_fixture())
            )
            result = _run(BambooHrStrategy().extract(_make_company(), _ctx()))
        meta = result["metadata"]
        for key in ("model", "agent_steps", "agent_completed", "agent_had_errors"):
            assert meta[key] is None, key

    def test_verdict_matches_expected_jobs(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_load_fixture())
            )
            result = _run(BambooHrStrategy().extract(_make_company(), _ctx()))
        assert result["metadata"]["verdict"] == "match"


class TestRetryPolicy:
    """One retry on transport errors and 5xx, then an error report."""

    def test_500_then_200_succeeds(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_ENDPOINT).mock(
                side_effect=[
                    httpx.Response(500),
                    httpx.Response(200, json=_load_fixture()),
                ]
            )
            result = _run(BambooHrStrategy().extract(_make_company(), _ctx()))
        assert result["metadata"]["error"] is None
        assert len(result["jobs"]) == 7

    def test_500_twice_yields_error_report(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_ENDPOINT).mock(
                side_effect=[httpx.Response(500), httpx.Response(500)]
            )
            result = _run(BambooHrStrategy().extract(_make_company(), _ctx()))
        assert result["jobs"] == []
        assert result["metadata"]["error"] is not None

    def test_transport_error_then_200_succeeds(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_ENDPOINT).mock(
                side_effect=[
                    httpx.ConnectError("boom"),
                    httpx.Response(200, json=_load_fixture()),
                ]
            )
            result = _run(BambooHrStrategy().extract(_make_company(), _ctx()))
        assert result["metadata"]["error"] is None
        assert len(result["jobs"]) == 7

    def test_404_yields_error_report(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_ENDPOINT).mock(return_value=httpx.Response(404))
            result = _run(BambooHrStrategy().extract(_make_company(), _ctx()))
        assert result["jobs"] == []
        assert result["metadata"]["error"] is not None


class TestPayloadShapeGuards:
    """Malformed envelopes fold into error reports; never raise out."""

    def test_missing_result_key(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_ENDPOINT).mock(
                return_value=httpx.Response(200, json={"meta": {}})
            )
            result = _run(BambooHrStrategy().extract(_make_company(), _ctx()))
        assert result["jobs"] == []
        assert "missing the 'result' key" in (result["metadata"]["error"] or "")

    def test_non_list_result(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_ENDPOINT).mock(
                return_value=httpx.Response(200, json={"result": {"a": 1}})
            )
            result = _run(BambooHrStrategy().extract(_make_company(), _ctx()))
        assert result["jobs"] == []
        assert "expected list" in (result["metadata"]["error"] or "")

    def test_non_object_body(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_ENDPOINT).mock(return_value=httpx.Response(200, json=[1, 2]))
            result = _run(BambooHrStrategy().extract(_make_company(), _ctx()))
        assert result["jobs"] == []
        assert "non-object body" in (result["metadata"]["error"] or "")

    def test_empty_result_is_honest_zero_not_error(self) -> None:
        # A tenant with no Costa Rica postings today is different from
        # a broken board.
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_ENDPOINT).mock(
                return_value=httpx.Response(200, json={"meta": {}, "result": []})
            )
            result = _run(BambooHrStrategy().extract(_make_company(), _ctx()))
        assert result["jobs"] == []
        assert result["metadata"]["error"] is None

    def test_generic_exception_is_absorbed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from vacantes.extraction.ats import bamboohr as bh

        def _boom(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("synthetic downstream failure")

        monkeypatch.setattr(bh, "select_region_urls", _boom)
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_load_fixture())
            )
            result = _run(BambooHrStrategy().extract(_make_company(), _ctx()))
        assert result["jobs"] == []
        assert "synthetic downstream failure" in (result["metadata"]["error"] or "")
