"""Unit tests for the PeopleForce strategy.

Structurally mirrors ``test_talentbrew.py`` — respx-mocked HTTP, a
recorded fixture replayed byte-for-byte, and a never-raises invariant —
with one class of test the sibling adapters have no equivalent for.

PeopleForce is the first strategy whose region scope is **discovered
per run** rather than declared in config. That is the whole reason it
exists (see the module docstring), so the discovery half carries the
weight here: which options are selected, that the selection survives a
board that adds a city, and that the two failure modes either side of
it — a board whose filter markup moved versus a board with no postings
in the region — stay distinguishable. Conflating those two would turn a
broken contract into a silent zero.

Both requests the adapter issues share one path (``/careers``) and are
told apart only by the ``location_id[]`` parameter, so the mock here is
a single dispatching route rather than two URL-matched ones — the same
discrimination the real endpoint makes.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from job_agent_lab.domain.company import Company
from job_agent_lab.domain.region import COSTA_RICA_LATAM
from job_agent_lab.extraction.ats.peopleforce import (
    PeopleForceStrategy,
    build_filtered_url,
    parse_location_options,
    select_region_locations,
)
from job_agent_lab.extraction.base import RunContext

FIXTURES = Path(__file__).parent.parent / "fixtures" / "api" / "peopleforce"
BOARD_URL = "https://planatechnologies.peopleforce.io/careers"

# The four options the recorded board offers inside the target region:
# three Costa Rica cities plus the tenant's own broader bucket.
CARTAGO, HEREDIA, SAN_JOSE, LATAM = "63939", "51094", "24253", "48932"

# The three postings the recorded filtered page carries.
RECORDED_JOB_IDS = {"229892", "215490", "194109"}

# A minimal board exposing exactly one in-region location, for tests
# that care about request flow rather than the recorded corpus.
ONE_CR_OPTION = (
    '<div data-cy="_location_id_select_63939_option" data-value="63939" '
    'data-text="Costa Rica/Cartago"></div>'
)


def _board_html() -> str:
    return (FIXTURES / "planatechnologies_board.html").read_text(encoding="utf-8")


def _filtered_html() -> str:
    return (FIXTURES / "planatechnologies_filtered.html").read_text(encoding="utf-8")


def _company(**overrides: object) -> Company:
    kwargs: dict[str, object] = {
        "name": "Plan A Technologies",
        "job_board_url": BOARD_URL,
        "sample_job_url": f"{BOARD_URL}/v/172812",
        "strategy": "peopleforce",
        "expected_jobs": 3,
    }
    kwargs.update(overrides)
    return Company(**kwargs)  # type: ignore[arg-type]


def _ctx() -> RunContext:
    return RunContext(
        model="unused", headless=True, max_steps=1, region=COSTA_RICA_LATAM
    )


def _is_filtered(request: httpx.Request) -> bool:
    """A request is the filtered fetch iff it carries the location array."""
    return "location_id" in str(request.url)


def _mock(
    board: str,
    filtered: str = "",
    *,
    responder: Callable[[httpx.Request], httpx.Response] | None = None,
    calls: list[httpx.Request] | None = None,
) -> respx.MockRouter:
    """Install one dispatching route for both requests the adapter makes."""
    router = respx.mock(assert_all_called=False)

    def _dispatch(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        if responder is not None:
            return responder(request)
        if _is_filtered(request):
            return httpx.Response(200, text=filtered)
        return httpx.Response(200, text=board)

    router.route(method="GET").mock(side_effect=_dispatch)
    return router


def _run(coro: Any) -> Any:
    """Drive a coroutine in a worker thread's fresh loop.

    Mirrors ``tests/unit/test_talentbrew._run``; see that file for why
    ``asyncio.run`` is unusable on the pytest main thread.
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


def _extract(
    router: respx.MockRouter, company: Company | None = None
) -> dict[str, Any]:
    with router:
        report = _run(PeopleForceStrategy().extract(company or _company(), _ctx()))
    assert isinstance(report, dict)
    return report


class TestParseLocationOptions:
    """The discovery parser against the recorded board."""

    def test_recorded_board_yields_every_location_option(self) -> None:
        assert len(parse_location_options(_board_html())) == 190

    def test_employment_type_options_are_excluded(self) -> None:
        # The board renders its employment-type filter with identical
        # data-value / data-text attributes; only the field-scoped
        # data-cy separates them. If that scoping regressed, these
        # labels would leak into the location set.
        labels = {label for _, label in parse_location_options(_board_html())}
        assert "Regular" not in labels
        assert "Part-time" not in labels

    def test_costa_rica_cities_are_present_with_their_ids(self) -> None:
        options = dict(parse_location_options(_board_html()))
        assert options[CARTAGO] == "Costa Rica/Cartago"
        assert options[HEREDIA] == "Costa Rica/Heredia"
        assert options[SAN_JOSE] == "Costa Rica/San José"

    def test_markup_without_options_yields_empty(self) -> None:
        assert parse_location_options("<html><body>no filters</body></html>") == []


class TestSelectRegionLocations:
    """Region selection — the behaviour that replaces a frozen list."""

    def test_recorded_board_selects_exactly_the_region(self) -> None:
        selected = select_region_locations(
            parse_location_options(_board_html()), COSTA_RICA_LATAM
        )
        assert [value for value, _ in selected] == [
            CARTAGO,
            HEREDIA,
            SAN_JOSE,
            LATAM,
        ]

    def test_out_of_region_options_are_rejected(self) -> None:
        assert (
            select_region_locations(
                [
                    ("1", "Colombia/Bogota"),
                    ("2", "United States"),
                    ("3", "India"),
                    ("4", "Europe"),
                ],
                COSTA_RICA_LATAM,
            )
            == []
        )

    def test_a_newly_added_city_is_picked_up(self) -> None:
        # The point of the whole adapter: this is the case a frozen
        # pre_filter_urls tuple misses silently, because the union still
        # equals the stale expected_jobs and the verdict stays "match".
        selected = select_region_locations(
            [
                (CARTAGO, "Costa Rica/Cartago"),
                ("77777", "Costa Rica/Alajuela"),
                ("88888", "Colombia/Medellin"),
            ],
            COSTA_RICA_LATAM,
        )
        assert [value for value, _ in selected] == [CARTAGO, "77777"]

    def test_duplicate_ids_collapse_preserving_order(self) -> None:
        selected = select_region_locations(
            [
                (HEREDIA, "Costa Rica/Heredia"),
                (CARTAGO, "Costa Rica/Cartago"),
                (HEREDIA, "Costa Rica/Heredia"),
            ],
            COSTA_RICA_LATAM,
        )
        assert [value for value, _ in selected] == [HEREDIA, CARTAGO]


class TestBuildFilteredUrl:
    """The one request that replaces one-request-per-city."""

    def test_repeats_the_array_parameter_per_location(self) -> None:
        url = build_filtered_url(BOARD_URL, [CARTAGO, HEREDIA], 1)
        assert url.count("location_id%5B%5D=") == 2
        assert CARTAGO in url and HEREDIA in url

    def test_page_one_carries_no_page_parameter(self) -> None:
        assert "page=" not in build_filtered_url(BOARD_URL, [CARTAGO], 1)

    def test_later_pages_carry_the_page_parameter(self) -> None:
        assert "page=3" in build_filtered_url(BOARD_URL, [CARTAGO], 3)

    def test_an_existing_query_string_is_discarded(self) -> None:
        # Inheriting a stale filter would silently narrow the result on
        # top of the filter this adapter is building.
        url = build_filtered_url(f"{BOARD_URL}?location_id=99999", [CARTAGO], 1)
        assert "99999" not in url


class TestRecordedPayload:
    """End-to-end replay of the recorded board + filtered pages."""

    def test_yields_the_three_recorded_postings(self) -> None:
        report = _extract(_mock(_board_html(), _filtered_html()))
        assert report["metadata"]["error"] is None
        assert report["metadata"]["total_jobs_found"] == 3
        found = {u.rsplit("/", 1)[-1].split("-")[0] for u in report["jobs"]}
        assert found == RECORDED_JOB_IDS

    def test_agent_fields_are_none(self) -> None:
        meta = _extract(_mock(_board_html(), _filtered_html()))["metadata"]
        assert meta["strategy"] == "peopleforce"
        for field in ("model", "agent_steps", "agent_completed", "agent_had_errors"):
            assert meta[field] is None

    def test_every_emitted_url_is_a_posting(self) -> None:
        report = _extract(_mock(_board_html(), _filtered_html()))
        assert report["jobs"]
        for url in report["jobs"]:
            assert url.startswith(f"{BOARD_URL}/v/")

    def test_the_filtered_request_carries_every_region_location(self) -> None:
        calls: list[httpx.Request] = []
        _extract(_mock(_board_html(), _filtered_html(), calls=calls))
        filtered = [str(r.url) for r in calls if _is_filtered(r)]
        assert filtered, "no filtered request was issued"
        for location_id in (CARTAGO, HEREDIA, SAN_JOSE, LATAM):
            assert location_id in filtered[0]

    def test_discovery_precedes_the_filtered_fetch(self) -> None:
        calls: list[httpx.Request] = []
        _extract(_mock(_board_html(), _filtered_html(), calls=calls))
        assert not _is_filtered(calls[0])
        assert _is_filtered(calls[1])


class TestEmptyAndErrorSemantics:
    """The split that keeps a broken board distinct from an empty one."""

    def test_no_location_options_is_an_error(self) -> None:
        # Zero options means the filter markup moved — a broken
        # contract, not an empty region.
        report = _extract(_mock("<html><body>redesigned</body></html>"))
        assert report["metadata"]["error"] is not None
        assert "location options" in report["metadata"]["error"]
        assert report["jobs"] == []

    def test_options_present_but_none_in_region_is_honest_empty(self) -> None:
        html = (
            '<div data-cy="_location_id_select_1_option" data-value="1" '
            'data-text="Colombia/Bogota"></div>'
            '<div data-cy="_location_id_select_2_option" data-value="2" '
            'data-text="India"></div>'
        )
        report = _extract(_mock(html))
        assert report["metadata"]["error"] is None
        assert report["jobs"] == []
        assert report["metadata"]["total_jobs_found"] == 0

    def test_no_filtered_request_when_region_is_empty(self) -> None:
        html = (
            '<div data-cy="_location_id_select_1_option" data-value="1" '
            'data-text="India"></div>'
        )
        calls: list[httpx.Request] = []
        _extract(_mock(html, calls=calls))
        assert not any(_is_filtered(r) for r in calls)

    def test_discovery_http_error_is_an_error_report(self) -> None:
        report = _extract(_mock("", responder=lambda _r: httpx.Response(404)))
        assert report["metadata"]["error"] is not None
        assert report["jobs"] == []

    def test_never_raises_on_transport_failure(self) -> None:
        def _boom(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        report = _extract(_mock("", responder=_boom))
        assert report["metadata"]["error"] is not None
        assert report["metadata"]["total_jobs_found"] == 0


class TestRetryPolicy:
    """One retry on transport errors and 5xx, none on 4xx."""

    def test_retries_once_then_succeeds(self) -> None:
        state = {"first": True}

        def _flaky(request: httpx.Request) -> httpx.Response:
            if _is_filtered(request):
                return httpx.Response(200, text=_filtered_html())
            if state["first"]:
                state["first"] = False
                return httpx.Response(503)
            return httpx.Response(200, text=_board_html())

        report = _extract(_mock("", responder=_flaky))
        assert report["metadata"]["error"] is None
        assert report["metadata"]["total_jobs_found"] == 3

    def test_two_server_errors_produce_an_error_report(self) -> None:
        report = _extract(_mock("", responder=lambda _r: httpx.Response(503)))
        assert report["metadata"]["error"] is not None

    def test_client_error_is_not_retried(self) -> None:
        calls: list[httpx.Request] = []
        _extract(_mock("", responder=lambda _r: httpx.Response(404), calls=calls))
        assert len(calls) == 1


class TestPagination:
    """Continue while a page adds postings; stop when it does not."""

    def test_second_page_is_unioned(self) -> None:
        def _paged(request: httpx.Request) -> httpx.Response:
            if not _is_filtered(request):
                return httpx.Response(200, text=ONE_CR_OPTION)
            body = (
                '<a href="/careers/v/222-beta"></a>'
                if "page=2" in str(request.url)
                else '<a href="/careers/v/111-alpha"></a>'
            )
            return httpx.Response(200, text=body)

        report = _extract(_mock("", responder=_paged))
        assert report["metadata"]["total_jobs_found"] == 2

    def test_a_board_ignoring_the_page_parameter_terminates(self) -> None:
        # Re-serving page 1 adds nothing new, which is the same
        # terminator as a natural last page — so a board that ignores
        # ``page`` cannot spin the loop.
        calls: list[httpx.Request] = []

        def _same(request: httpx.Request) -> httpx.Response:
            if not _is_filtered(request):
                return httpx.Response(200, text=ONE_CR_OPTION)
            return httpx.Response(200, text='<a href="/careers/v/111-alpha"></a>')

        report = _extract(_mock("", responder=_same, calls=calls))
        assert report["metadata"]["total_jobs_found"] == 1
        assert sum(1 for r in calls if _is_filtered(r)) == 2

    def test_chrome_anchors_are_dropped_by_the_link_rule(self) -> None:
        def _noisy(request: httpx.Request) -> httpx.Response:
            if not _is_filtered(request):
                return httpx.Response(200, text=ONE_CR_OPTION)
            return httpx.Response(
                200,
                text=(
                    '<a href="/careers/v/111-alpha"></a>'
                    '<a href="/careers">back</a>'
                    '<a href="https://elsewhere.example/careers/v/999"></a>'
                ),
            )

        report = _extract(_mock("", responder=_noisy))
        assert report["jobs"] == [f"{BOARD_URL}/v/111-alpha"]


class TestHostValidator:
    """``strategy="peopleforce"`` is gated on the platform host."""

    def test_tenant_subdomain_is_accepted(self) -> None:
        assert _company().strategy == "peopleforce"

    def test_marketing_site_is_rejected(self) -> None:
        with pytest.raises(Exception, match="peopleforce"):
            _company(job_board_url="https://www.planatechnologies.com/careers")

    def test_bare_platform_domain_is_rejected(self) -> None:
        with pytest.raises(Exception, match="peopleforce"):
            _company(job_board_url="https://peopleforce.io/careers")
