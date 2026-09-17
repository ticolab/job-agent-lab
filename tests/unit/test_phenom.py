"""Unit tests for the Phenom ``refineSearch`` adapter.

Two layers, matching the plan's split:

- **Pure helpers** — the slug rule, the request-body template, and URL
  synthesis are ordinary functions with no HTTP, so they are tested
  directly. The slug rule is *inferred* from observed board URLs rather
  than documented by Phenom, which makes pinning it here load-bearing:
  a silent change would produce URLs the board does not serve, and the
  only other guard is the manual HTTP-200 verification performed once
  per tenant at integration.

- **The adapter**, driven through ``respx`` so no network is touched.
  The happy path replays the recorded payload at
  ``tests/fixtures/api/phenom/bcg.json``, seeded from the original
  evidence capture. That fixture is BCG's regression artifact *in place of*
  a DOM
  snapshot — the substitution that closes C12 — so it is asserted
  against exactly, including the 14-job count it froze. The live board
  has since drifted to 13; fixtures record history, live runs record
  the present, and conflating the two is what the verdict layer
  exists to surface.

The ``_run`` worker-thread helper is copied from
``test_greenhouse.py``; see its docstring for why ``asyncio.run`` is
unusable on the pytest main thread here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from vacantes.domain.company import Company, LinkRule, PhenomConfig
from vacantes.domain.region import COSTA_RICA_LATAM
from vacantes.extraction.ats.phenom import (
    PhenomStrategy,
    _record_looks_in_region,
    build_request_body,
    slugify_title,
    synthesize_job_url,
)
from vacantes.extraction.base import RunContext

# The tenant-hosted endpoint the adapter POSTs to. Composed from the
# company's ``job_board_url`` origin plus ``PhenomConfig.endpoint_path``
# — kept literal here so the respx routes stay readable and so a change
# to either half fails loudly (respx raises on an un-mocked host).
_ORIGIN = "https://careers.bcg.com"
_ENDPOINT = f"{_ORIGIN}/widgets"
_PATH_PREFIX = "/global/en/job"

_FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "api" / "phenom"


def _load_fixture(name: str = "bcg") -> dict[str, Any]:
    """Read a recorded Phenom ``/widgets`` payload."""
    payload = json.loads((_FIXTURE_DIR / f"{name}.json").read_text())
    assert isinstance(payload, dict), f"fixture {name!r} is not a JSON object"
    return payload


def _make_company(**overrides: Any) -> Company:
    """Build a BCG-shaped ``strategy="phenom"`` company."""
    defaults: dict[str, Any] = {
        "name": "BCG",
        "job_board_url": f"{_ORIGIN}/global/en/search-results",
        "sample_job_url": f"{_ORIGIN}/global/en/job/58329/A-Title",
        "strategy": "phenom",
        "phenom": PhenomConfig(page_id="page17-ds"),
        "link_rule": LinkRule(path_prefix=_PATH_PREFIX),
        "expected_jobs": 14,
    }
    defaults.update(overrides)
    return Company(**defaults)


def _ctx() -> RunContext:
    return RunContext(
        model="gpt-4.1-mini",
        headless=True,
        max_steps=20,
        region=COSTA_RICA_LATAM,
    )


def _run(coro: Any) -> Any:
    """Drive a coroutine in a worker thread's fresh loop.

    See ``test_greenhouse._run`` for the full rationale: pytest-playwright
    leaves a running-loop entry on the main thread that makes both
    ``asyncio.run`` and ``loop.run_until_complete`` refuse to start.
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


class TestSlugifyTitle:
    """The inferred slug rule, pinned case by case.

    Case is preserved deliberately — Phenom serves mixed-case slugs, so
    lowercasing (as ``catalog.slugify`` does, for a different purpose)
    would yield URLs the board does not serve.
    """

    def test_parenthesised_suffix(self) -> None:
        # The acceptance-criteria case from the ticket.
        assert slugify_title("Creative Manager (Hybrid)") == "Creative-Manager-Hybrid"

    def test_ampersand_and_hyphen_separator(self) -> None:
        # Live-verified against BCG on 2026-08-02: this exact title's
        # synthesized URL returned HTTP 200.
        assert (
            slugify_title(
                "Change & Communications Senior Coordinator - Global Practices"
            )
            == "Change-Communications-Senior-Coordinator-Global-Practices"
        )

    def test_multiple_spaces_collapse_to_one_hyphen(self) -> None:
        assert slugify_title("Senior    Data   Engineer") == "Senior-Data-Engineer"

    def test_trailing_punctuation_is_trimmed(self) -> None:
        assert slugify_title("Data Engineer!!!") == "Data-Engineer"

    def test_leading_punctuation_is_trimmed(self) -> None:
        assert slugify_title("--- Data Engineer") == "Data-Engineer"

    def test_case_is_preserved(self) -> None:
        assert slugify_title("TEMP: Global Marketing Manager") == (
            "TEMP-Global-Marketing-Manager"
        )

    def test_digits_survive(self) -> None:
        assert slugify_title("Analyst II - 2026 Cohort") == "Analyst-II-2026-Cohort"

    def test_title_without_alphanumerics_yields_empty_string(self) -> None:
        # The adapter treats this as unusable and skips the record
        # rather than emitting a URL with an empty tail segment.
        assert slugify_title("!!! ---") == ""


class TestBuildRequestBody:
    """The wire contract, expressed in exactly one place."""

    def test_matches_the_documented_template(self) -> None:
        body = build_request_body(PhenomConfig(page_id="page17-ds"), "Costa Rica")
        assert body == {
            "lang": "en_global",
            "pageName": "search-results",
            "ddoKey": "refineSearch",
            "from": 0,
            "size": 100,
            "jobs": True,
            "counts": True,
            "all_fields": [
                "country",
                "city",
                "category",
                "company",
                "type",
                "jobType",
            ],
            "pageId": "page17-ds",
            "siteType": "external",
            "selected_fields": {"country": ["Costa Rica"]},
            "keywords": "",
            "global": True,
            "locationData": {},
        }

    def test_config_overrides_flow_into_the_body(self) -> None:
        body = build_request_body(
            PhenomConfig(page_id="page99-x", locale="es_global"), "Costa Rica"
        )
        assert body["pageId"] == "page99-x"
        assert body["lang"] == "es_global"

    def test_single_call_page_size(self) -> None:
        # One call is the whole contract; see the module docstring on
        # why there is no pagination loop.
        assert (
            build_request_body(PhenomConfig(page_id="p"), "Costa Rica")["size"] == 100
        )


class TestSynthesizeJobUrl:
    """URL synthesis — the one inferred contract in the R2 architecture."""

    def test_shape(self) -> None:
        assert (
            synthesize_job_url(
                _ORIGIN, _PATH_PREFIX, "58329", "Creative Manager (Hybrid)"
            )
            == f"{_ORIGIN}{_PATH_PREFIX}/58329/Creative-Manager-Hybrid"
        )

    def test_path_prefix_is_not_hardcoded(self) -> None:
        # A second Phenom tenant with a different route must need
        # config, not a code change.
        assert (
            synthesize_job_url(
                "https://careers.example.com", "/en/careers/job", "7", "Data Engineer"
            )
            == "https://careers.example.com/en/careers/job/7/Data-Engineer"
        )


class TestExtractHappyPathRecorded:
    """The recorded payload replays to 14 synthesized URLs.

    This fixture is BCG's regression artifact in place of a DOM
    snapshot, so the assertions here are the C12 closure in executable
    form.
    """

    def test_recorded_payload_yields_fourteen_urls(self) -> None:
        payload = _load_fixture()
        with respx.mock(assert_all_called=True) as mock:
            mock.post(_ENDPOINT).mock(return_value=httpx.Response(200, json=payload))
            result = _run(PhenomStrategy().extract(_make_company(), _ctx()))

        meta = result["metadata"]
        assert meta["error"] is None
        assert meta["strategy"] == "phenom"
        # API strategies report honest nulls for every agent-only field.
        assert meta["model"] is None
        assert meta["agent_steps"] is None
        assert meta["agent_completed"] is None
        assert meta["agent_had_errors"] is None
        # verdict fields flow automatically through build_report.
        assert meta["expected_jobs"] == 14
        assert meta["verdict"] == "match"
        assert meta["total_jobs_found"] == 14
        assert len(result["jobs"]) == 14

    def test_every_url_has_the_synthesized_shape(self) -> None:
        payload = _load_fixture()
        with respx.mock(assert_all_called=True) as mock:
            mock.post(_ENDPOINT).mock(return_value=httpx.Response(200, json=payload))
            result = _run(PhenomStrategy().extract(_make_company(), _ctx()))

        prefix = f"{_ORIGIN}{_PATH_PREFIX}/"
        for url in result["jobs"]:
            assert url.startswith(prefix), url
            tail = url[len(prefix) :]
            job_id, _, slug = tail.partition("/")
            assert job_id.isdigit(), url
            assert slug, url

    def test_known_record_pins_a_full_url(self) -> None:
        # jobId 58329 with a title carrying both '&' and ' - '; this
        # exact URL was verified HTTP 200 against the live board on
        # 2026-08-02.
        payload = _load_fixture()
        with respx.mock(assert_all_called=True) as mock:
            mock.post(_ENDPOINT).mock(return_value=httpx.Response(200, json=payload))
            result = _run(PhenomStrategy().extract(_make_company(), _ctx()))

        assert (
            f"{_ORIGIN}{_PATH_PREFIX}/58329/"
            "Change-Communications-Senior-Coordinator-Global-Practices"
        ) in result["jobs"]


class TestRequestContract:
    """What the adapter actually puts on the wire."""

    def test_body_carries_the_facet_and_tenant_fields(self) -> None:
        payload = _load_fixture()
        captured: dict[str, Any] = {}

        def _capture(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(200, json=payload)

        with respx.mock(assert_all_called=True) as mock:
            mock.post(_ENDPOINT).mock(side_effect=_capture)
            _run(PhenomStrategy().extract(_make_company(), _ctx()))

        assert captured["ddoKey"] == "refineSearch"
        assert captured["size"] == 100
        assert captured["pageId"] == "page17-ds"
        # The facet-value decision: the region's canonical country name,
        # not the whole preferred-half slice (which also contains "CR",
        # a DOM-filter token the API returns nothing for).
        assert captured["selected_fields"] == {"country": ["Costa Rica"]}

    def test_no_csrf_or_referer_headers_are_required(self) -> None:
        # The captured evidence curl carried x-csrf-token and referer,
        # but the live probe on 2026-08-02 answered 200 without them.
        # Pinning their absence keeps a future contributor from
        # reintroducing session-bootstrapping the endpoint never needed.
        payload = _load_fixture()
        captured_headers: dict[str, str] = {}

        def _capture(request: httpx.Request) -> httpx.Response:
            captured_headers.update(dict(request.headers))
            return httpx.Response(200, json=payload)

        with respx.mock(assert_all_called=True) as mock:
            mock.post(_ENDPOINT).mock(side_effect=_capture)
            _run(PhenomStrategy().extract(_make_company(), _ctx()))

        assert "x-csrf-token" not in captured_headers
        assert "referer" not in captured_headers
        assert captured_headers["content-type"] == "application/json"


class TestRetryAndFailureSemantics:
    """One transient retry; hard failures surface in ``metadata.error``."""

    def test_retry_then_success(self) -> None:
        payload = _load_fixture()
        with respx.mock(assert_all_called=True) as mock:
            mock.post(_ENDPOINT).mock(
                side_effect=[
                    httpx.Response(503),
                    httpx.Response(200, json=payload),
                ]
            )
            result = _run(PhenomStrategy().extract(_make_company(), _ctx()))

        assert result["metadata"]["error"] is None
        assert len(result["jobs"]) == 14

    def test_two_server_errors_produce_an_error_report(self) -> None:
        with respx.mock(assert_all_called=True) as mock:
            mock.post(_ENDPOINT).mock(
                side_effect=[httpx.Response(503), httpx.Response(503)]
            )
            result = _run(PhenomStrategy().extract(_make_company(), _ctx()))

        assert result["jobs"] == []
        assert result["metadata"]["error"] is not None
        assert "503" in result["metadata"]["error"]

    def test_transport_error_retries_then_reports(self) -> None:
        with respx.mock(assert_all_called=True) as mock:
            mock.post(_ENDPOINT).mock(
                side_effect=httpx.ConnectError("connection refused")
            )
            result = _run(PhenomStrategy().extract(_make_company(), _ctx()))

        assert result["jobs"] == []
        assert "ConnectError" in result["metadata"]["error"]

    def test_transport_error_then_success(self) -> None:
        payload = _load_fixture()
        with respx.mock(assert_all_called=True) as mock:
            mock.post(_ENDPOINT).mock(
                side_effect=[
                    httpx.ConnectError("flaky"),
                    httpx.Response(200, json=payload),
                ]
            )
            result = _run(PhenomStrategy().extract(_make_company(), _ctx()))

        assert result["metadata"]["error"] is None
        assert len(result["jobs"]) == 14

    @pytest.mark.parametrize(
        ("body", "expected_fragment"),
        [
            ({}, "refineSearch"),
            ({"refineSearch": {}}, "refineSearch.data"),
            ({"refineSearch": {"data": {}}}, "jobs"),
            ({"refineSearch": {"data": {"jobs": "nope"}}}, "jobs"),
        ],
    )
    def test_malformed_payloads_produce_error_reports(
        self, body: dict[str, Any], expected_fragment: str
    ) -> None:
        with respx.mock(assert_all_called=True) as mock:
            mock.post(_ENDPOINT).mock(return_value=httpx.Response(200, json=body))
            result = _run(PhenomStrategy().extract(_make_company(), _ctx()))

        assert result["jobs"] == []
        assert expected_fragment in result["metadata"]["error"]


class TestHonestEmpty:
    """Zero region matches is a result, not a failure."""

    def test_empty_job_list_is_not_an_error(self) -> None:
        payload = _load_fixture()
        payload["refineSearch"]["data"]["jobs"] = []
        payload["refineSearch"]["totalHits"] = 0
        with respx.mock(assert_all_called=True) as mock:
            mock.post(_ENDPOINT).mock(return_value=httpx.Response(200, json=payload))
            result = _run(PhenomStrategy().extract(_make_company(), _ctx()))

        assert result["jobs"] == []
        assert result["metadata"]["error"] is None
        # A tenant with no CR postings today is a legitimate outcome;
        # the verdict layer classifies it against expected_jobs.
        assert result["metadata"]["verdict"] == "under"


class TestServerFacetIsAuthoritative:
    """The region facet decides membership; the string check only reports.

    This class replaced a ``TestClientSideReverification`` that asserted
    the opposite — that a record whose location text does not name the
    region is dropped. Hewlett Packard Enterprise disproved that policy:
    five genuine Costa Rica postings arrive under the Costa Rica facet
    with ``country="India"`` and their Costa Rica half written
    ``"San Jose, San Jose, 00000"``, with no country name anywhere for a
    string rule to find. Teaching the predicate ``San Jose`` is not
    available either — it is Zscaler's California headquarters, worth 58
    phantom postings in that tenant's recorded fixture.

    So the emit loop keeps every record the server returned and logs the
    ones whose text does not visibly name the region. That aligns Phenom
    with the Talentbrew adapter, which documents the same stance and
    ships no client-side re-check at all. The trade is explicit: a
    genuinely mis-faceted record would now be emitted rather than
    silently dropped, and the log plus the verdict layer are what
    surface it.
    """

    def test_a_record_the_text_cannot_place_is_kept_not_dropped(self) -> None:
        # The HPE shape, reduced: the server returned it under the Costa
        # Rica facet, but nothing in its text says so. Under the old
        # policy this was dropped and the count silently short.
        payload = _load_fixture()
        payload["refineSearch"]["data"]["jobs"].append(
            {
                "jobId": "99999",
                "title": "Technical Courseware Developer",
                "country": "India",
                "city": "Bengaluru",
                "multi_location_array": [
                    {"location": "Bengaluru, Karnataka, 560048"},
                    {"location": "San Jose, San Jose, 00000"},
                ],
            }
        )
        with respx.mock(assert_all_called=True) as mock:
            mock.post(_ENDPOINT).mock(return_value=httpx.Response(200, json=payload))
            result = _run(PhenomStrategy().extract(_make_company(), _ctx()))

        assert len(result["jobs"]) == 15, "the facet's record must survive"
        assert any("99999" in url for url in result["jobs"])

    def test_the_disagreement_is_logged_so_it_is_not_silent(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Keeping the record is only defensible if the disagreement is
        # visible; an operator reading batch logs is the detector.
        payload = _load_fixture()
        payload["refineSearch"]["data"]["jobs"] = [
            {
                "jobId": "99999",
                "title": "Technical Courseware Developer",
                "country": "India",
                "city": "Bengaluru",
                "multi_location_array": [{"location": "San Jose, San Jose, 00000"}],
            }
        ]
        with respx.mock(assert_all_called=True) as mock:
            mock.post(_ENDPOINT).mock(return_value=httpx.Response(200, json=payload))
            with caplog.at_level(logging.INFO, logger="vacantes.extraction.ats.phenom"):
                result = _run(PhenomStrategy().extract(_make_company(), _ctx()))

        assert len(result["jobs"]) == 1
        assert any(
            "does not visibly name region" in r.getMessage() for r in caplog.records
        )
        assert any("99999" in r.getMessage() for r in caplog.records)

    def test_a_record_whose_text_does_name_the_region_logs_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The signal must stay quiet on the normal case, or it is noise
        # and an operator will learn to ignore it.
        payload = _load_fixture()
        payload["refineSearch"]["data"]["jobs"] = [
            {
                "jobId": "12345",
                "title": "Network Support Engineer",
                "country": "Costa Rica",
                "city": "Heredia",
                "multi_location_array": [{"country": "Costa Rica", "city": "Heredia"}],
            }
        ]
        with respx.mock(assert_all_called=True) as mock:
            mock.post(_ENDPOINT).mock(return_value=httpx.Response(200, json=payload))
            with caplog.at_level(logging.INFO, logger="vacantes.extraction.ats.phenom"):
                result = _run(PhenomStrategy().extract(_make_company(), _ctx()))

        assert len(result["jobs"]) == 1
        assert not [
            r
            for r in caplog.records
            if "does not visibly name region" in r.getMessage()
        ]

    def test_recorded_multi_location_record_with_non_regional_primary_is_kept(
        self,
    ) -> None:
        # The record that exposed the too-narrow first rule for this
        # adapter (consult the array only when the primary country is
        # empty). Job 57516 is a genuine
        # Costa Rica posting (returned under the CR facet, counted in
        # totalHits: 14) whose *primary* fields read United Kingdom /
        # London, with Heredia, Costa Rica only in
        # multi_location_array. Consulting the array solely when the
        # primary country is empty would drop it, emitting 13 where the
        # server says 14 — a silent under-count biased against exactly
        # the multi-region postings BCG publishes most.
        payload = _load_fixture()
        record = next(
            j
            for j in payload["refineSearch"]["data"]["jobs"]
            if str(j["jobId"]) == "57516"
        )
        assert record["country"] == "United Kingdom"
        assert any(e["country"] == "Costa Rica" for e in record["multi_location_array"])

        with respx.mock(assert_all_called=True) as mock:
            mock.post(_ENDPOINT).mock(return_value=httpx.Response(200, json=payload))
            result = _run(PhenomStrategy().extract(_make_company(), _ctx()))

        assert any("/57516/" in url for url in result["jobs"])

    def test_multi_location_array_rescues_an_empty_primary_country(self) -> None:
        # The case §4.8.1 describes explicitly; a natural subset of the
        # wider rule above.
        payload = _load_fixture()
        payload["refineSearch"]["data"]["jobs"] = [
            {
                "jobId": "77777",
                "title": "Roaming Consultant",
                "country": "",
                "city": "",
                "multi_location_array": [
                    {"country": "Germany", "city": "Berlin"},
                    {"country": "Costa Rica", "city": "Heredia"},
                ],
            }
        ]
        with respx.mock(assert_all_called=True) as mock:
            mock.post(_ENDPOINT).mock(return_value=httpx.Response(200, json=payload))
            result = _run(PhenomStrategy().extract(_make_company(), _ctx()))

        assert result["jobs"] == [f"{_ORIGIN}{_PATH_PREFIX}/77777/Roaming-Consultant"]

    def test_multi_location_array_without_a_regional_entry_is_still_kept(self) -> None:
        # Deliberately the inverse of the old assertion. The server was
        # asked for Costa Rica and returned this; the adapter cannot
        # distinguish "Phenom promoted the wrong location and the array
        # is incomplete" from "the facet is wrong", and the HPE evidence
        # says the former is what actually happens. Emitting with a log
        # is the honest reading; the verdict layer catches the other case
        # as an over-count.
        payload = _load_fixture()
        payload["refineSearch"]["data"]["jobs"] = [
            {
                "jobId": "77778",
                "title": "Roaming Consultant",
                "country": "",
                "city": "",
                "multi_location_array": [
                    {"country": "Germany", "city": "Berlin"},
                    {"country": "France", "city": "Paris"},
                ],
            }
        ]
        with respx.mock(assert_all_called=True) as mock:
            mock.post(_ENDPOINT).mock(return_value=httpx.Response(200, json=payload))
            result = _run(PhenomStrategy().extract(_make_company(), _ctx()))

        assert len(result["jobs"]) == 1
        assert result["metadata"]["error"] is None


class TestConfigFaultsAreReportedNotRaised:
    """Config problems surface as error reports, keeping the CLI loop alive."""

    def test_missing_path_prefix_is_an_error_report(self) -> None:
        # Enforced at extract time rather than schema time: the payload
        # carries no board URL to validate against at import, and
        # deriving from sample_job_url would keep the jobId segment and
        # silently synthesize garbage.
        company = _make_company(link_rule=LinkRule())
        # ``assert_all_called=False`` is the point of the test, not a
        # concession: the config fault must short-circuit before any
        # request is issued, so the mounted route is expected to stay
        # uncalled and the assertion below pins that.
        with respx.mock(assert_all_called=False) as mock:
            route = mock.post(_ENDPOINT).mock(return_value=httpx.Response(200, json={}))
            result = _run(PhenomStrategy().extract(company, _ctx()))

        assert result["jobs"] == []
        assert "link_rule.path_prefix" in result["metadata"]["error"]
        # The fault is caught before any HTTP call is attempted.
        assert not route.called


class TestUnderFetchWarning:
    """``totalHits`` beyond the page size is logged, not silently dropped."""

    def test_warns_when_total_hits_exceeds_returned_records(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        payload = _load_fixture()
        payload["refineSearch"]["totalHits"] = 250
        with respx.mock(assert_all_called=True) as mock:
            mock.post(_ENDPOINT).mock(return_value=httpx.Response(200, json=payload))
            with caplog.at_level(logging.WARNING):
                result = _run(PhenomStrategy().extract(_make_company(), _ctx()))

        # The received subset is still emitted — the verdict layer, not
        # the adapter, decides what a shortfall means.
        assert len(result["jobs"]) == 14
        assert result["metadata"]["error"] is None
        assert any("totalHits=250" in r.getMessage() for r in caplog.records)


class TestDeduplication:
    """A repeated record collapses to one emitted URL."""

    def test_duplicate_job_id_yields_one_url(self) -> None:
        payload = _load_fixture()
        jobs = payload["refineSearch"]["data"]["jobs"]
        jobs.append(deepcopy(jobs[0]))
        with respx.mock(assert_all_called=True) as mock:
            mock.post(_ENDPOINT).mock(return_value=httpx.Response(200, json=payload))
            result = _run(PhenomStrategy().extract(_make_company(), _ctx()))

        assert len(result["jobs"]) == 14
        assert len(set(result["jobs"])) == 14


_ROCHE_ORIGIN = "https://careers.roche.com"
_ROCHE_ENDPOINT = f"{_ROCHE_ORIGIN}/widgets"


def _make_roche_company(**overrides: Any) -> Company:
    """Build the shipped ``Roche`` catalog entry's shape."""
    defaults: dict[str, Any] = {
        "name": "Roche",
        "job_board_url": f"{_ROCHE_ORIGIN}/global/en/search-results",
        "sample_job_url": f"{_ROCHE_ORIGIN}/global/en/job/202605-113325/A-Title",
        "strategy": "phenom",
        "phenom": PhenomConfig(page_id="page11-ds"),
        "link_rule": LinkRule(path_prefix=_PATH_PREFIX),
        "expected_jobs": 12,
    }
    defaults.update(overrides)
    return Company(**defaults)


class TestMultiLocationEntryShapes:
    """``multi_location_array`` entries come in two tenant shapes.

    BCG emits discrete ``country`` / ``city`` keys. Roche emits
    ``{latlong, location}`` where ``location`` is a pre-joined
    ``"Sabana Norte, San Jose, Costa Rica"`` string and neither
    discrete key is present. Reading only the discrete pair made the
    multi-location arm a silent no-op on the second shape: the loop
    ran, matched nothing, and dropped genuine in-region postings whose
    primary fields pointed elsewhere.

    These cases pin both shapes on the same code path so a future
    narrowing cannot re-break either.
    """

    def test_discrete_country_city_shape_matches(self) -> None:
        job = {
            "country": "United Kingdom",
            "city": "London",
            "multi_location_array": [{"country": "Costa Rica", "city": "Heredia"}],
        }
        assert _record_looks_in_region(job, COSTA_RICA_LATAM) is True

    def test_joined_location_string_shape_matches(self) -> None:
        job = {
            "country": "Hungary",
            "city": "Budapest",
            "multi_location_array": [
                {
                    "latlong": {"lon": 19.04, "lat": 47.49},
                    "location": "Budapest, Pest, Hungary",
                },
                {
                    "latlong": {"lon": -84.10, "lat": 9.93},
                    "location": "Sabana Norte, San Jose, Costa Rica",
                },
            ],
        }
        assert _record_looks_in_region(job, COSTA_RICA_LATAM) is True

    def test_joined_location_string_out_of_region_does_not_match(self) -> None:
        job = {
            "country": "Hungary",
            "city": "Budapest",
            "multi_location_array": [
                {
                    "latlong": {"lon": 19.04, "lat": 47.49},
                    "location": "Budapest, Pest, Hungary",
                },
            ],
        }
        assert _record_looks_in_region(job, COSTA_RICA_LATAM) is False

    def test_discrete_pair_wins_when_both_present(self) -> None:
        # A malformed entry carrying both must not double-count or
        # crash; the discrete pair is read first and short-circuits.
        job = {
            "country": "Hungary",
            "city": "Budapest",
            "multi_location_array": [
                {"country": "Costa Rica", "city": "Heredia", "location": "irrelevant"}
            ],
        }
        assert _record_looks_in_region(job, COSTA_RICA_LATAM) is True

    def test_non_string_location_value_is_tolerated(self) -> None:
        job = {
            "country": "Hungary",
            "city": "Budapest",
            "multi_location_array": [{"location": {"unexpected": "object"}}],
        }
        assert _record_looks_in_region(job, COSTA_RICA_LATAM) is False


class TestRocheRecordedPayload:
    """The live-recorded Roche board, replayed through respx.

    Roche is the second Phenom tenant and the one that exposed the
    entry-shape gap above. Its Costa Rica facet reports
    ``totalHits: 12``, of which three postings carry non-Costa-Rica
    primary fields (two Budapest, one Indianapolis) and reach the
    region only through the joined-string multi-location entries.
    """

    def test_recorded_payload_yields_twelve_urls(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.post(_ROCHE_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_load_fixture("roche"))
            )
            result = _run(PhenomStrategy().extract(_make_roche_company(), _ctx()))
        assert result["metadata"]["error"] is None
        assert len(result["jobs"]) == 12

    def test_multi_location_postings_are_included(self) -> None:
        # The three that the pre-fix adapter dropped. Each is a genuine
        # Costa Rica posting whose primary country is elsewhere.
        with respx.mock(assert_all_called=False) as mock:
            mock.post(_ROCHE_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_load_fixture("roche"))
            )
            result = _run(PhenomStrategy().extract(_make_roche_company(), _ctx()))
        for job_id in ("202607-117361", "202608-120466", "202605-113728"):
            assert any(f"/job/{job_id}/" in u for u in result["jobs"]), job_id

    def test_every_url_is_absolutized_to_roche_origin(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.post(_ROCHE_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_load_fixture("roche"))
            )
            result = _run(PhenomStrategy().extract(_make_roche_company(), _ctx()))
        for url in result["jobs"]:
            assert url.startswith(f"{_ROCHE_ORIGIN}{_PATH_PREFIX}/"), url

    def test_emitted_count_equals_server_total_hits(self) -> None:
        # The adapter must not under-report against the server's own
        # count — the exact failure the entry-shape gap produced (9/12).
        payload = _load_fixture("roche")
        total = payload["refineSearch"]["totalHits"]
        with respx.mock(assert_all_called=False) as mock:
            mock.post(_ROCHE_ENDPOINT).mock(
                return_value=httpx.Response(200, json=payload)
            )
            result = _run(PhenomStrategy().extract(_make_roche_company(), _ctx()))
        assert len(result["jobs"]) == total


_PHILIPS_ORIGIN = "https://www.careers.philips.com"
_PHILIPS_ENDPOINT = f"{_PHILIPS_ORIGIN}/widgets"


def _make_philips_company(**overrides: Any) -> Company:
    """Build the shipped ``Philips`` catalog entry's shape."""
    defaults: dict[str, Any] = {
        "name": "Philips",
        "job_board_url": f"{_PHILIPS_ORIGIN}/global/en/search-results",
        "sample_job_url": (
            f"{_PHILIPS_ORIGIN}/global/en/job/583860/Finance-Controller-Assistant"
        ),
        "strategy": "phenom",
        "phenom": PhenomConfig(page_id="page31-ds"),
        "link_rule": LinkRule(path_prefix=_PATH_PREFIX),
        "expected_jobs": 1,
    }
    defaults.update(overrides)
    return Company(**defaults)


class TestPhilipsRecordedPayload:
    """Third Phenom tenant — the single-posting floor of the adapter.

    Philips matters for two reasons beyond "one more tenant".

    First, it is the corpus's smallest non-zero API result: the Costa
    Rica facet reports ``totalHits: 1``. BCG (13) and Roche (12) both
    exercise the many-record path, so a regression that, say, returned
    the first page's head or mishandled a one-element list would pass
    both and fail here. The count assertions below are deliberately
    tied to the payload's own ``totalHits`` as well as the literal 1,
    so the fixture and the adapter cannot drift apart silently.

    Second, it pins a *synthesized* URL end to end. The adapter builds
    posting URLs from the record's job id plus a slugified title rather
    than reading a href from the payload, which carries a verification
    obligation — a wrong slug still yields a well-formed URL. The
    single record's synthesized URL was checked against the live board
    on 2026-08-31 and resolves to the real posting ("Labeling
    Specialist job in Alajuela, Alajuela, Costa Rica"), so the exact
    string is pinned here as the regression anchor for the slug rule.
    """

    def test_recorded_payload_yields_one_url(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.post(_PHILIPS_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_load_fixture("philips"))
            )
            result = _run(PhenomStrategy().extract(_make_philips_company(), _ctx()))
        assert result["metadata"]["error"] is None
        assert len(result["jobs"]) == 1

    def test_emitted_count_equals_server_total_hits(self) -> None:
        payload = _load_fixture("philips")
        total = payload["refineSearch"]["totalHits"]
        with respx.mock(assert_all_called=False) as mock:
            mock.post(_PHILIPS_ENDPOINT).mock(
                return_value=httpx.Response(200, json=payload)
            )
            result = _run(PhenomStrategy().extract(_make_philips_company(), _ctx()))
        assert len(result["jobs"]) == total == 1

    def test_synthesized_url_matches_the_verified_live_posting(self) -> None:
        # Verified against the live board on 2026-08-31. If the slug
        # rule changes, this fails rather than silently emitting a
        # well-formed 404.
        with respx.mock(assert_all_called=False) as mock:
            mock.post(_PHILIPS_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_load_fixture("philips"))
            )
            result = _run(PhenomStrategy().extract(_make_philips_company(), _ctx()))
        assert result["jobs"] == [
            f"{_PHILIPS_ORIGIN}{_PATH_PREFIX}/580020/Labeling-Specialist"
        ]

    def test_page_id_is_tenant_specific(self) -> None:
        # page31-ds, read off a live /widgets capture. Distinct from
        # BCG's page17-ds and Roche's page11-ds — unlike Talentbrew's
        # country facet, the Phenom page id genuinely does not transfer
        # between tenants, so a copy-paste would silently query the
        # wrong board.
        philips_cfg = _make_philips_company().phenom
        roche_cfg = _make_roche_company().phenom
        assert philips_cfg is not None and roche_cfg is not None
        assert philips_cfg.page_id == "page31-ds"
        assert philips_cfg.page_id != roche_cfg.page_id


_ZB_ORIGIN = "https://careers.zimmerbiomet.com"
_ZB_ENDPOINT = f"{_ZB_ORIGIN}/widgets"
_ZB_PATH_PREFIX = "/us/en/job"


def _make_zimmer_company(**overrides: Any) -> Company:
    """Build the shipped ``Zimmer Biomet`` catalog entry's shape."""
    defaults: dict[str, Any] = {
        "name": "Zimmer Biomet",
        "job_board_url": f"{_ZB_ORIGIN}/us/en/search-results",
        "sample_job_url": f"{_ZB_ORIGIN}/us/en/job/11373/Operations-Project-Manager",
        "strategy": "phenom",
        "phenom": PhenomConfig(page_id="page12-ds", locale="en_us"),
        "link_rule": LinkRule(path_prefix=_ZB_PATH_PREFIX),
        "expected_jobs": 11,
    }
    defaults.update(overrides)
    return Company(**defaults)


class TestZimmerBiometRecordedPayload:
    """Fourth Phenom tenant — first non-``en_global`` locale and non-global path.

    Every prior tenant (BCG, Roche, Philips) sits at the ``en_global``
    locale default and serves postings under ``/global/en/job``. Zimmer
    Biomet is the first to differ on both axes at once: its board issues
    ``lang: "en_us"`` and its postings live under ``/us/en/job``. That
    makes it the entry that would catch a regression hard-coding either
    value — a change assuming ``/global/en/job`` still returns 11
    well-formed URLs here, all of them 404s.

    Both locales were probed live on 2026-08-31 and return the identical
    11 records, so ``en_us`` is shipped for fidelity to the tenant's own
    request rather than out of necessity; ``test_locale_is_the_tenants_own``
    pins that choice so a future "simplify to the default" edit is a
    deliberate decision rather than an accident.

    The synthesized-URL obligation gets its sharpest test here. Two of
    the eleven titles carry characters the slug rule must survive —
    ``Finance Manager (Green Valley manufacturing site)`` (parentheses)
    and ``Supply Chain Manager (m/f/d)`` (parentheses plus slashes).
    Both synthesized URLs were opened against the live board on
    2026-08-31 and resolve to the real Costa Rica postings, so they are
    pinned literally below.
    """

    def test_recorded_payload_yields_eleven_urls(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.post(_ZB_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_load_fixture("zimmer_biomet"))
            )
            result = _run(PhenomStrategy().extract(_make_zimmer_company(), _ctx()))
        assert result["metadata"]["error"] is None
        assert len(result["jobs"]) == 11

    def test_emitted_count_equals_server_total_hits(self) -> None:
        payload = _load_fixture("zimmer_biomet")
        total = payload["refineSearch"]["totalHits"]
        with respx.mock(assert_all_called=False) as mock:
            mock.post(_ZB_ENDPOINT).mock(return_value=httpx.Response(200, json=payload))
            result = _run(PhenomStrategy().extract(_make_zimmer_company(), _ctx()))
        assert len(result["jobs"]) == total == 11

    def test_every_url_uses_the_us_en_locale_path(self) -> None:
        # The locale segment is part of the posting path, not just the
        # board URL. A regression defaulting to /global/en/job would
        # still emit 11 well-formed URLs, every one a 404.
        with respx.mock(assert_all_called=False) as mock:
            mock.post(_ZB_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_load_fixture("zimmer_biomet"))
            )
            result = _run(PhenomStrategy().extract(_make_zimmer_company(), _ctx()))
        for url in result["jobs"]:
            assert url.startswith(f"{_ZB_ORIGIN}{_ZB_PATH_PREFIX}/"), url

    def test_punctuated_titles_slugify_to_verified_live_urls(self) -> None:
        # Verified against the live board on 2026-08-31. Parentheses are
        # dropped and the slashes in "(m/f/d)" become hyphens; both
        # resolve to the real postings.
        with respx.mock(assert_all_called=False) as mock:
            mock.post(_ZB_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_load_fixture("zimmer_biomet"))
            )
            result = _run(PhenomStrategy().extract(_make_zimmer_company(), _ctx()))
        assert (
            f"{_ZB_ORIGIN}{_ZB_PATH_PREFIX}/11571/Supply-Chain-Manager-m-f-d"
            in result["jobs"]
        )
        assert (
            f"{_ZB_ORIGIN}{_ZB_PATH_PREFIX}"
            "/10091/Finance-Manager-Green-Valley-manufacturing-site" in result["jobs"]
        )

    def test_locale_is_the_tenants_own(self) -> None:
        # en_us, read off the live board. en_global returns the same 11
        # records, so this is fidelity rather than necessity — but the
        # value shipped should be the one the tenant issues.
        cfg = _make_zimmer_company().phenom
        assert cfg is not None
        assert cfg.locale == "en_us"
        assert cfg.page_id == "page12-ds"


_TDSYNNEX_ORIGIN = "https://careers.tdsynnex.com"
_TDSYNNEX_ENDPOINT = f"{_TDSYNNEX_ORIGIN}/widgets"


def _make_tdsynnex_company(**overrides: Any) -> Company:
    """Build the shipped ``TD SYNNEX`` catalog entry's shape."""
    defaults: dict[str, Any] = {
        "name": "TD SYNNEX",
        "job_board_url": f"{_TDSYNNEX_ORIGIN}/us/en/search-results",
        "sample_job_url": (
            f"{_TDSYNNEX_ORIGIN}/us/en/job/R55610/Technical-Support-Technician-CR"
        ),
        "link_rule": LinkRule(path_prefix="/us/en/job"),
        "strategy": "phenom",
        "expected_jobs": 28,
        "phenom": PhenomConfig(page_id="page11-ds", locale="en_us"),
    }
    defaults.update(overrides)
    return Company(**defaults)


class TestTdSynnexRecordedPayload:
    """Fifth Phenom tenant — the punctuation ceiling of the slug rule.

    TD SYNNEX is the corpus's largest Phenom result at 28 (BCG 13,
    Roche 12, Zimmer Biomet 11, Philips 1), but its real contribution is
    to the *synthesized URL* obligation rather than to record count.

    The adapter builds every posting URL from a job id plus a slugified
    title, so a wrong slug rule still yields a well-formed URL that
    404s — which is why the module docstring requires at least three
    synthesized URLs to be checked live before an integration lands. All
    28 were checked on 2026-09-16 and every one returned HTTP 200.

    What makes this tenant worth a fixture is *which* titles it carries.
    Phenom's earlier tenants verified ``&`` and `` - ``; TD SYNNEX adds
    a title containing a forward slash, ``PingOne Developer / IAM
    Developer``. That one is materially riskier than the others: every
    other punctuation mark degrades to a hyphen or vanishes, but a
    surviving ``/`` would inject an extra *path segment* and change the
    URL's shape rather than merely its spelling — a 404 that no count
    assertion anywhere would notice. The three pins below cover the
    slash, the ampersand (dropped, not expanded to ``and``), and the
    spaced hyphen, each against its live-verified string.
    """

    def test_recorded_payload_yields_twenty_eight_urls(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.post(_TDSYNNEX_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_load_fixture("tdsynnex"))
            )
            result = _run(PhenomStrategy().extract(_make_tdsynnex_company(), _ctx()))
        assert result["metadata"]["error"] is None
        assert len(result["jobs"]) == 28

    def test_emitted_count_equals_server_total_hits(self) -> None:
        payload = _load_fixture("tdsynnex")
        total = payload["refineSearch"]["totalHits"]
        with respx.mock(assert_all_called=False) as mock:
            mock.post(_TDSYNNEX_ENDPOINT).mock(
                return_value=httpx.Response(200, json=payload)
            )
            result = _run(PhenomStrategy().extract(_make_tdsynnex_company(), _ctx()))
        assert len(result["jobs"]) == total == 28

    def test_a_slash_in_the_title_does_not_become_a_path_segment(self) -> None:
        # "PingOne Developer / IAM Developer". A surviving slash would
        # add a path segment, not just misspell the slug, so assert the
        # exact live-verified string AND the segment count.
        with respx.mock(assert_all_called=False) as mock:
            mock.post(_TDSYNNEX_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_load_fixture("tdsynnex"))
            )
            result = _run(PhenomStrategy().extract(_make_tdsynnex_company(), _ctx()))
        expected = (
            f"{_TDSYNNEX_ORIGIN}/us/en/job/R53516/PingOne-Developer-IAM-Developer"
        )
        assert expected in result["jobs"]
        tail = expected.split("/us/en/job/", 1)[1]
        assert tail.count("/") == 1, tail

    def test_ampersand_and_spaced_hyphen_slugs_match_live_postings(self) -> None:
        # Both verified against the live board on 2026-09-16. The
        # ampersand is dropped rather than expanded to "and".
        with respx.mock(assert_all_called=False) as mock:
            mock.post(_TDSYNNEX_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_load_fixture("tdsynnex"))
            )
            result = _run(PhenomStrategy().extract(_make_tdsynnex_company(), _ctx()))
        assert (
            f"{_TDSYNNEX_ORIGIN}/us/en/job/R53498/"
            "Cybersecurity-Automation-Continuous-Compliance-Analyst"
        ) in result["jobs"]
        assert (
            f"{_TDSYNNEX_ORIGIN}/us/en/job/R55610/Technical-Support-Technician-CR"
        ) in result["jobs"]

    def test_every_url_is_under_the_us_locale_job_prefix(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.post(_TDSYNNEX_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_load_fixture("tdsynnex"))
            )
            result = _run(PhenomStrategy().extract(_make_tdsynnex_company(), _ctx()))
        for url in result["jobs"]:
            assert url.startswith(f"{_TDSYNNEX_ORIGIN}/us/en/job/"), url


_HPE_ORIGIN = "https://careers.hpe.com"
_HPE_ENDPOINT = f"{_HPE_ORIGIN}/widgets"


def _make_hpe_company(**overrides: Any) -> Company:
    """Build the shipped ``Hewlett Packard Enterprise`` entry's shape."""
    defaults: dict[str, Any] = {
        "name": "Hewlett Packard Enterprise",
        "job_board_url": f"{_HPE_ORIGIN}/us/en/search-results",
        "sample_job_url": (
            f"{_HPE_ORIGIN}/us/en/job/1207481/Technical-Courseware-Developer"
        ),
        "link_rule": LinkRule(path_prefix="/us/en/job"),
        "strategy": "phenom",
        "expected_jobs": 27,
        "phenom": PhenomConfig(page_id="page15", locale="en_us"),
    }
    defaults.update(overrides)
    return Company(**defaults)


class TestHpeRecordedPayload:
    """Sixth Phenom tenant — the payload that retired the client-side gate.

    HPE is the evidence that a location *string* cannot be relied on to
    name its own country. Five of its 27 Costa Rica postings — all
    ``Technical Courseware Developer``, jobIds 1206368, 1206423, 1207479,
    1207481, 1207483 — come back under the Costa Rica facet with
    ``country="India"``, ``city="Bengaluru"``, and their Costa Rica half
    present only as ``"San Jose, San Jose, 00000"``: city, state,
    postcode, no country. The entry's ``latlong`` (lon -84.08, lat 9.93)
    is unambiguously San José, Costa Rica, and the queue's own
    ``sample_job_url`` is one of the five.

    Under the old rejecting re-check the adapter emitted 22 against a
    server ``totalHits`` of 27 — a silent under-count. Teaching the
    predicate ``San Jose`` was never an option: it is Zscaler's
    California headquarters and would add 58 phantom postings to that
    tenant's fixture. So the facet became authoritative and this fixture
    pins that it stays so.
    """

    def test_recorded_payload_yields_twenty_seven_urls(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.post(_HPE_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_load_fixture("hpe"))
            )
            result = _run(PhenomStrategy().extract(_make_hpe_company(), _ctx()))
        assert result["metadata"]["error"] is None
        assert len(result["jobs"]) == 27

    def test_emitted_count_equals_server_total_hits(self) -> None:
        payload = _load_fixture("hpe")
        total = payload["refineSearch"]["totalHits"]
        with respx.mock(assert_all_called=False) as mock:
            mock.post(_HPE_ENDPOINT).mock(
                return_value=httpx.Response(200, json=payload)
            )
            result = _run(PhenomStrategy().extract(_make_hpe_company(), _ctx()))
        assert len(result["jobs"]) == total == 27

    def test_the_five_unnamed_country_records_survive(self) -> None:
        # The regression this fixture exists for. Each of these is a real
        # Costa Rica posting whose text says only "San Jose, San Jose,
        # 00000"; a rejecting string check drops all five.
        with respx.mock(assert_all_called=False) as mock:
            mock.post(_HPE_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_load_fixture("hpe"))
            )
            result = _run(PhenomStrategy().extract(_make_hpe_company(), _ctx()))
        for job_id in ("1206368", "1206423", "1207479", "1207481", "1207483"):
            assert any(f"/us/en/job/{job_id}/" in url for url in result["jobs"]), (
                f"jobId {job_id} was dropped"
            )

    def test_those_records_do_not_visibly_name_the_region(self) -> None:
        # Guards the premise rather than the outcome: if a future payload
        # started naming the country, this fixture would stop exercising
        # the case it was recorded for and should be re-captured.
        payload = _load_fixture("hpe")
        jobs = payload["refineSearch"]["data"]["jobs"]
        unnamed = [j for j in jobs if not _record_looks_in_region(j, COSTA_RICA_LATAM)]
        assert len(unnamed) == 5
        assert {str(j["jobId"]) for j in unnamed} == {
            "1206368",
            "1206423",
            "1207479",
            "1207481",
            "1207483",
        }

    def test_every_url_is_under_the_us_locale_job_prefix(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.post(_HPE_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_load_fixture("hpe"))
            )
            result = _run(PhenomStrategy().extract(_make_hpe_company(), _ctx()))
        for url in result["jobs"]:
            assert url.startswith(f"{_HPE_ORIGIN}/us/en/job/"), url


_CISCO_ORIGIN = "https://careers.cisco.com"
_CISCO_ENDPOINT = f"{_CISCO_ORIGIN}/widgets"


def _make_cisco_company(**overrides: Any) -> Company:
    """Build the shipped ``Cisco`` catalog entry's shape."""
    defaults: dict[str, Any] = {
        "name": "Cisco",
        "job_board_url": f"{_CISCO_ORIGIN}/global/en/search-results",
        "sample_job_url": (
            f"{_CISCO_ORIGIN}/global/en/job/2023794/"
            "Renewals-Specialist-Splunk-COE-Hybrid"
        ),
        "link_rule": LinkRule(path_prefix="/global/en/job"),
        "strategy": "phenom",
        "expected_jobs": 1,
        "phenom": PhenomConfig(page_id="page4", locale="en_global"),
    }
    defaults.update(overrides)
    return Company(**defaults)


class TestCiscoRecordedPayload:
    """Seventh Phenom tenant — mostly corpus completeness, with one novelty.

    Cisco breaks little new ground and this docstring says so rather than
    inventing significance: Philips already pins the single-record floor,
    and parenthesised titles are already covered by the HPE, Roche,
    TD SYNNEX and Zimmer Biomet fixtures. The fixture exists because
    every Phenom tenant ships one — the strategy whitelist in
    ``test_company_schema`` enforces it — and because it pins this
    tenant's config, which is the part that rots.

    The one genuine novelty is a config-space point: ``page4`` is a
    suffix-less page id on the ``en_global`` locale. HPE introduced
    suffix-less ids but on ``en_us``; every ``en_global`` tenant before
    Cisco used the ``-ds`` form. Together they establish that the
    adapter treats ``page_id`` as opaque, which is the intended
    contract — there is no rule to derive it, it is read off a live
    ``/widgets`` body at integration time.

    Worth recording separately: Cisco is the first tenant where the
    queue's human-supplied ``sample_job_url`` is byte-identical to the
    URL :func:`synthesize_job_url` produces from the payload, on a title
    carrying both a spaced hyphen and parentheses
    (``"Renewals Specialist - Splunk COE (Hybrid)"``). That is an
    outside check on the slug rule rather than the adapter agreeing with
    itself.
    """

    def test_recorded_payload_yields_one_url(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.post(_CISCO_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_load_fixture("cisco"))
            )
            result = _run(PhenomStrategy().extract(_make_cisco_company(), _ctx()))
        assert result["metadata"]["error"] is None
        assert len(result["jobs"]) == 1

    def test_emitted_count_equals_server_total_hits(self) -> None:
        payload = _load_fixture("cisco")
        total = payload["refineSearch"]["totalHits"]
        with respx.mock(assert_all_called=False) as mock:
            mock.post(_CISCO_ENDPOINT).mock(
                return_value=httpx.Response(200, json=payload)
            )
            result = _run(PhenomStrategy().extract(_make_cisco_company(), _ctx()))
        assert len(result["jobs"]) == total == 1

    def test_synthesized_url_equals_the_queue_supplied_sample(self) -> None:
        # The outside check: a human wrote this URL by copying it off the
        # live board, and the slug rule reproduces it byte for byte from
        # "Renewals Specialist - Splunk COE (Hybrid)" — spaced hyphen
        # collapsed, parentheses dropped. Verified HTTP 200 on
        # 2026-09-17. If the slug rule drifts this fails rather than
        # emitting a well-formed 404.
        with respx.mock(assert_all_called=False) as mock:
            mock.post(_CISCO_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_load_fixture("cisco"))
            )
            result = _run(PhenomStrategy().extract(_make_cisco_company(), _ctx()))
        assert result["jobs"] == [
            f"{_CISCO_ORIGIN}/global/en/job/2023794/"
            "Renewals-Specialist-Splunk-COE-Hybrid"
        ]
