"""Unit tests for the Talentbrew adapter's pure helpers (SYS-17 Task 2).

Two layers, following the phenom.py precedent:

- **Pure helpers** (this file, Task 2) — parser, LinkRule mirror, and
  query-parameter builder are ordinary functions with no HTTP, tested
  directly.
- **The adapter** (Task 4) — driven through ``respx`` so no network
  is touched; the happy path replays the recorded payload at
  ``tests/fixtures/api/talentbrew/citi.json`` (seeded from the
  evidence capture at ``spike/evidence/citi_results_cr.json``).

Design authority: ``spike/ARCHITECTURE_PROPOSAL_R2.md`` §4.8.2.
JS matcher parity coverage lives in
``tests/snapshots/test_linkrule_parity.py`` (SYS-17 Task 5); this file
pins the Python-side semantics directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
import pytest
import respx

from vacantes.domain.company import Company, LinkRule, TalentbrewConfig
from vacantes.domain.region import COSTA_RICA_LATAM
from vacantes.extraction.ats.talentbrew import (
    TalentbrewStrategy,
    apply_link_rule,
    build_query_params,
    parse_anchor_hrefs,
)
from vacantes.extraction.base import RunContext

# ---------------------------------------------------------------------------
# parse_anchor_hrefs
# ---------------------------------------------------------------------------


class TestParseAnchorHrefs:
    """Pin the stdlib-``HTMLParser`` anchor extraction contract.

    The parser is deliberately tolerant (``HTMLParser`` never raises
    on malformed HTML), and the caller relies on
    ``convert_charrefs=True`` — the default — to unescape entities
    inside attribute values for free. Both properties are pinned
    below.
    """

    def test_returns_hrefs_in_document_order(self) -> None:
        fragment = (
            '<a href="/job/alpha">A</a>'
            '<a href="/job/beta">B</a>'
            '<a href="/job/gamma">C</a>'
        )
        assert parse_anchor_hrefs(fragment) == [
            "/job/alpha",
            "/job/beta",
            "/job/gamma",
        ]

    def test_skips_non_anchor_tags(self) -> None:
        fragment = (
            '<div><button data-id="3624060">Country: Costa Rica</button>'
            '<a href="/job/one">One</a>'
            '<img src="/logo.png"/>'
            '<a href="/job/two">Two</a></div>'
        )
        assert parse_anchor_hrefs(fragment) == ["/job/one", "/job/two"]

    def test_skips_anchors_without_href(self) -> None:
        # ``<a name="…">`` and JS-widget hooks without ``href`` carry
        # no navigable URL — dropping them keeps the LinkRule mirror's
        # input clean.
        fragment = '<a name="top">Skip</a><a href="/job/real">Real</a><a>Bare</a>'
        assert parse_anchor_hrefs(fragment) == ["/job/real"]

    def test_empty_fragment_returns_empty_list(self) -> None:
        assert parse_anchor_hrefs("") == []

    def test_preserves_duplicates(self) -> None:
        # Dedup is the LinkRule mirror's job; the parser must not
        # collapse duplicates or the parity test cannot exercise the
        # mirror's dedup branch.
        fragment = '<a href="/job/x">1</a><a href="/job/x">2</a>'
        assert parse_anchor_hrefs(fragment) == ["/job/x", "/job/x"]

    def test_unescapes_entities_in_href(self) -> None:
        # ``convert_charrefs=True`` (the default) unescapes ``&amp;``
        # inside attribute values. The captured Citi fragment has
        # entities in titles but not in hrefs, so this synthetic
        # input pins the guarantee end-to-end.
        fragment = '<a href="/job/one?x=1&amp;y=2">Job</a>'
        assert parse_anchor_hrefs(fragment) == ["/job/one?x=1&y=2"]

    def test_multiline_and_whitespace_tolerated(self) -> None:
        fragment = (
            '<a\n  class="sr-job-item__link"\n  href="/job/heredia/x/287/1"\n>Job</a>'
        )
        assert parse_anchor_hrefs(fragment) == ["/job/heredia/x/287/1"]


# ---------------------------------------------------------------------------
# apply_link_rule — the Python mirror of collect_links.js
# ---------------------------------------------------------------------------


_CITI_ORIGIN = "https://jobs.citi.com"


class TestApplyLinkRule:
    """Pin the URL-layer semantics mirrored from ``collect_links.js``.

    The parity test in ``tests/snapshots/test_linkrule_parity.py`` is
    the mechanical guard against JS↔Python drift; this class fixes
    the *Python-side* contract directly so a mirror regression fails
    a fast unit test as well as the slower Chromium parity test.
    """

    def test_id_in_path_bucket_basic(self) -> None:
        urls = [
            "https://jobs.citi.com/job/heredia/backend/287/12345",
            "https://jobs.citi.com/job/heredia/frontend/287/67890",
        ]
        assert apply_link_rule(urls, origin=_CITI_ORIGIN, base_path="/job") == [
            "https://jobs.citi.com/job/heredia/backend/287/12345",
            "https://jobs.citi.com/job/heredia/frontend/287/67890",
        ]

    def test_id_in_query_bucket_basic(self) -> None:
        # Path equals base_path exactly and there is a non-empty query
        # — the Akurey / 10Pearls id-in-query shape.
        urls = ["https://jobs.citi.com/careers?pId=180"]
        assert apply_link_rule(urls, origin=_CITI_ORIGIN, base_path="/careers") == [
            "https://jobs.citi.com/careers?pId=180"
        ]

    def test_path_bucket_preferred_when_both_populated(self) -> None:
        # Lever-style boards where the id-in-query shape on the
        # listing root is a filter facet; the JS matcher's
        # path-bucket preference drops the facet URLs when real
        # postings exist.
        urls = [
            "https://jobs.citi.com/careers?facet=eng",  # query bucket
            "https://jobs.citi.com/careers/12345",  # path bucket
        ]
        assert apply_link_rule(urls, origin=_CITI_ORIGIN, base_path="/careers") == [
            "https://jobs.citi.com/careers/12345"
        ]

    def test_path_bucket_drained_by_min_depth_falls_back_to_query(self) -> None:
        # A path bucket fully drained by min_depth is indistinguishable
        # from a naturally empty one — the query bucket wins.
        urls = [
            "https://jobs.citi.com/careers?facet=eng",
            "https://jobs.citi.com/careers/chrome",  # depth 1, dropped
        ]
        assert apply_link_rule(
            urls, origin=_CITI_ORIGIN, base_path="/careers", min_depth=2
        ) == ["https://jobs.citi.com/careers?facet=eng"]

    def test_min_depth_floor(self) -> None:
        urls = [
            "https://jobs.citi.com/careers/culture",  # depth 1 — dropped
            "https://jobs.citi.com/careers/eng/senior-123",  # depth 2 — kept
        ]
        assert apply_link_rule(
            urls, origin=_CITI_ORIGIN, base_path="/careers", min_depth=2
        ) == ["https://jobs.citi.com/careers/eng/senior-123"]

    def test_cross_origin_dropped(self) -> None:
        urls = [
            "https://apply.other.com/job/heredia/backend/287/1",
            "https://jobs.citi.com/job/heredia/backend/287/2",
        ]
        assert apply_link_rule(urls, origin=_CITI_ORIGIN, base_path="/job") == [
            "https://jobs.citi.com/job/heredia/backend/287/2",
        ]

    def test_relative_url_dropped(self) -> None:
        # The adapter is responsible for pre-absolutizing; a relative
        # URL that reaches this function is silently dropped by the
        # origin gate (empty scheme/netloc).
        urls = ["/job/heredia/backend/287/1"]
        assert apply_link_rule(urls, origin=_CITI_ORIGIN, base_path="/job") == []

    def test_unparseable_url_dropped_silently(self) -> None:
        # A URL with an invalid IPv6-shape host raises ``ValueError``
        # from ``urlsplit`` when the port is read; caught and
        # dropped.
        urls = [
            "http://[bad::port:99999]/",
            "https://jobs.citi.com/job/heredia/backend/287/keep",
        ]
        assert apply_link_rule(urls, origin=_CITI_ORIGIN, base_path="/job") == [
            "https://jobs.citi.com/job/heredia/backend/287/keep",
        ]

    def test_empty_string_dropped(self) -> None:
        assert apply_link_rule(
            ["", "https://jobs.citi.com/job/heredia/x/287/1"],
            origin=_CITI_ORIGIN,
            base_path="/job",
        ) == ["https://jobs.citi.com/job/heredia/x/287/1"]

    def test_fragment_stripped_from_emitted_url(self) -> None:
        # ``/jobs/123`` and ``/jobs/123#apply`` collapse to one entry;
        # the emitted URL never carries a fragment.
        urls = [
            "https://jobs.citi.com/job/heredia/x/287/1#apply",
            "https://jobs.citi.com/job/heredia/x/287/1",
        ]
        assert apply_link_rule(urls, origin=_CITI_ORIGIN, base_path="/job") == [
            "https://jobs.citi.com/job/heredia/x/287/1",
        ]

    def test_trailing_slash_only_normalises_for_bucket_check(self) -> None:
        # Path bucket: trailing slash normalises for the ``startsWith``
        # check but the emitted URL retains the original pathname (JS
        # behaviour — the bucket set stores ``linkUrl.href``, not
        # ``linkPath``).
        urls = ["https://jobs.citi.com/job/heredia/x/287/1/"]
        assert apply_link_rule(urls, origin=_CITI_ORIGIN, base_path="/job") == [
            "https://jobs.citi.com/job/heredia/x/287/1/",
        ]

    def test_trailing_slash_on_base_path_only_falls_to_query_bucket(self) -> None:
        # ``/careers/`` normalises to ``/careers`` for the bucket
        # check, so it hits the id-in-query branch (path == base_path)
        # only when a query is present.
        assert (
            apply_link_rule(
                ["https://jobs.citi.com/careers/"],
                origin=_CITI_ORIGIN,
                base_path="/careers",
            )
            == []
        )
        assert apply_link_rule(
            ["https://jobs.citi.com/careers/?pId=180"],
            origin=_CITI_ORIGIN,
            base_path="/careers",
        ) == ["https://jobs.citi.com/careers/?pId=180"]

    def test_id_in_query_requires_non_empty_query(self) -> None:
        # ``path === basePath`` but empty query — dropped. Prevents
        # a bare listing-root URL from surviving as a spurious posting.
        assert (
            apply_link_rule(
                ["https://jobs.citi.com/careers"],
                origin=_CITI_ORIGIN,
                base_path="/careers",
            )
            == []
        )

    def test_dedup_by_emitted_url_string(self) -> None:
        # Same URL appearing twice collapses; the JS uses a Set on
        # ``linkUrl.href``, we mirror by emitting each URL only once
        # in first-seen order.
        urls = [
            "https://jobs.citi.com/job/heredia/x/287/1",
            "https://jobs.citi.com/job/heredia/x/287/1",
            "https://jobs.citi.com/job/heredia/x/287/2",
        ]
        assert apply_link_rule(urls, origin=_CITI_ORIGIN, base_path="/job") == [
            "https://jobs.citi.com/job/heredia/x/287/1",
            "https://jobs.citi.com/job/heredia/x/287/2",
        ]

    def test_first_seen_order_preserved(self) -> None:
        # Two anchors at different depths still emit in input order,
        # unrelated to depth or dict iteration.
        urls = [
            "https://jobs.citi.com/job/b/287/2",
            "https://jobs.citi.com/job/a/287/1",
        ]
        assert apply_link_rule(urls, origin=_CITI_ORIGIN, base_path="/job") == [
            "https://jobs.citi.com/job/b/287/2",
            "https://jobs.citi.com/job/a/287/1",
        ]

    def test_default_min_depth_matches_link_rule_default(self) -> None:
        # ``min_depth`` defaults to 1, matching
        # ``LinkRule.min_depth``'s default. Depth-1 posting is kept.
        assert apply_link_rule(
            ["https://jobs.citi.com/job/one"],
            origin=_CITI_ORIGIN,
            base_path="/job",
        ) == ["https://jobs.citi.com/job/one"]

    def test_empty_input_returns_empty_list(self) -> None:
        assert apply_link_rule([], origin=_CITI_ORIGIN, base_path="/job") == []

    def test_iterable_input_accepted(self) -> None:
        # The signature accepts any ``Iterable[str]`` — a generator
        # must work as well as a list.
        def _gen() -> Iterator[str]:
            yield "https://jobs.citi.com/job/heredia/x/287/1"
            yield "https://jobs.citi.com/job/heredia/x/287/2"

        assert apply_link_rule(_gen(), origin=_CITI_ORIGIN, base_path="/job") == [
            "https://jobs.citi.com/job/heredia/x/287/1",
            "https://jobs.citi.com/job/heredia/x/287/2",
        ]


# ---------------------------------------------------------------------------
# build_query_params
# ---------------------------------------------------------------------------


class TestBuildQueryParams:
    """Pin the single wire-contract expression.

    Values that come from :class:`TalentbrewConfig` or from the page
    argument are asserted directly; the captured constants are pinned
    verbatim so a silent drift in any of them fails the test.
    """

    def _config(self, records_per_page: int = 15) -> TalentbrewConfig:
        return TalentbrewConfig(
            facet_id="3624060",
            facet_display="Costa Rica",
            records_per_page=records_per_page,
        )

    def test_config_derived_variables(self) -> None:
        params = build_query_params(self._config(), page=1)
        assert params["ActiveFacetID"] == "3624060"
        assert params["FacetFilters[0].ID"] == "3624060"
        assert params["FacetFilters[0].Display"] == "Costa Rica"
        assert params["RecordsPerPage"] == "15"

    def test_current_page_reflects_page_argument(self) -> None:
        assert build_query_params(self._config(), page=1)["CurrentPage"] == "1"
        assert build_query_params(self._config(), page=2)["CurrentPage"] == "2"
        assert build_query_params(self._config(), page=17)["CurrentPage"] == "17"

    def test_records_per_page_reflects_config(self) -> None:
        assert (
            build_query_params(self._config(records_per_page=50), page=1)[
                "RecordsPerPage"
            ]
            == "50"
        )

    def test_captured_constants_verbatim(self) -> None:
        # Pin every constant against the evidence curl at
        # ``spike/evidence/citi_results_cr.curl.txt``. A silent
        # change to any of these fails here.
        params = build_query_params(self._config(), page=1)
        assert params["TotalContentResults"] == ""
        assert params["Distance"] == "50"
        assert params["RadiusUnitType"] == "0"
        assert params["Keywords"] == ""
        assert params["Location"] == ""
        assert params["ShowRadius"] == "False"
        assert params["IsPagination"] == "False"
        assert params["CustomFacetName"] == ""
        assert params["FacetTerm"] == ""
        assert params["FacetType"] == "0"
        assert params["FacetFilters[0].FacetType"] == "2"
        assert params["FacetFilters[0].Count"] == "10"
        assert params["FacetFilters[0].IsApplied"] == "true"
        assert params["FacetFilters[0].FieldName"] == ""
        assert params["SearchResultsModuleName"] == "Search Results"
        assert params["SearchFiltersModuleName"] == "Search Filters"
        assert params["SortCriteria"] == "5"
        assert params["SortDirection"] == "0"
        assert params["SearchType"] == "5"
        assert params["PostalCode"] == ""
        assert params["ResultsType"] == "0"
        assert params["fc"] == ""
        assert params["fl"] == ""
        assert params["fcf"] == ""
        assert params["afc"] == ""
        assert params["afl"] == ""
        assert params["afcf"] == ""
        assert params["TotalContentPages"] == "NaN"

    def test_is_pagination_stays_false_on_subsequent_pages(self) -> None:
        # See the module docstring on ``build_query_params``: the
        # multi-page toggle is unverified for Citi and remains
        # ``"False"`` verbatim until a multi-page tenant arrives with
        # live evidence to justify flipping it.
        assert build_query_params(self._config(), page=2)["IsPagination"] == "False"

    def test_all_values_are_strings(self) -> None:
        # httpx accepts str values as-is; typing the whole dict as
        # ``dict[str, str]`` keeps callers honest.
        params = build_query_params(self._config(), page=3)
        for key, value in params.items():
            assert isinstance(value, str), f"{key!r} is not a str"

    def test_key_set_matches_captured_evidence(self) -> None:
        # The full key set below is copy-pasted from the evidence
        # curl's query string (order-preserved insertion; dict
        # equality is order-independent). A missing key or a stray
        # extra key fails here.
        params = build_query_params(self._config(), page=1)
        expected_keys = {
            "ActiveFacetID",
            "CurrentPage",
            "RecordsPerPage",
            "TotalContentResults",
            "Distance",
            "RadiusUnitType",
            "Keywords",
            "Location",
            "ShowRadius",
            "IsPagination",
            "CustomFacetName",
            "FacetTerm",
            "FacetType",
            "FacetFilters[0].ID",
            "FacetFilters[0].FacetType",
            "FacetFilters[0].Count",
            "FacetFilters[0].Display",
            "FacetFilters[0].IsApplied",
            "FacetFilters[0].FieldName",
            "SearchResultsModuleName",
            "SearchFiltersModuleName",
            "SortCriteria",
            "SortDirection",
            "SearchType",
            "PostalCode",
            "ResultsType",
            "fc",
            "fl",
            "fcf",
            "afc",
            "afl",
            "afcf",
            "TotalContentPages",
        }
        assert set(params.keys()) == expected_keys


# ---------------------------------------------------------------------------
# TalentbrewStrategy (Task 3 — respx-mocked adapter tests)
# ---------------------------------------------------------------------------

# Citi is the SYS-17 anchor tenant. Composed from the recorded
# ``spike/evidence/citi_results_cr.curl.txt`` URL so any mismatch
# between the test setup and the evidence surfaces as a failed respx
# route match rather than a silent skip.
_CITI_ORIGIN = "https://jobs.citi.com"
_CITI_ENDPOINT = f"{_CITI_ORIGIN}/search-jobs/results"
_CITI_PATH_PREFIX = "/job"

_FIXTURE_DIR = (
    Path(__file__).resolve().parent.parent / "fixtures" / "api" / "talentbrew"
)


def _load_citi_fixture() -> dict[str, Any]:
    """Read the recorded Citi ``/search-jobs/results`` payload."""
    payload = json.loads((_FIXTURE_DIR / "citi.json").read_text())
    assert isinstance(payload, dict), "citi.json is not a JSON object"
    return payload


def _make_company(**overrides: Any) -> Company:
    """Build a Citi-shaped ``strategy=\"talentbrew\"`` company."""
    from vacantes.domain.company import TalentbrewConfig

    defaults: dict[str, Any] = {
        "name": "Citi",
        "job_board_url": f"{_CITI_ORIGIN}/search-jobs",
        "sample_job_url": (
            f"{_CITI_ORIGIN}/job/heredia/mca-lead-analyst/287/93146003136"
        ),
        "strategy": "talentbrew",
        "talentbrew": TalentbrewConfig(
            facet_id="3624060",
            facet_display="Costa Rica",
            # Pin explicitly to 10 to match the recorded fixture's
            # anchor count and to make the multi-page synth tests
            # legible. The schema default of 15 is Citi's own widget
            # setting but not the recorded-fixture size.
            records_per_page=10,
        ),
        "link_rule": LinkRule(path_prefix=_CITI_PATH_PREFIX),
        "expected_jobs": 10,
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

    Mirrors ``tests/unit/test_phenom._run``; see that file's docstring
    for why ``asyncio.run`` is unusable on the pytest main thread.
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


def _empty_page_payload() -> dict[str, Any]:
    """A well-shaped envelope whose ``results`` is anchor-free.

    Used both as a page-N terminator (``hasJobs=false``) and, with
    ``hasJobs=True``, as the anchor for the page-1 zero-anchors loud
    guard.
    """
    return {
        "filters": '<div class="filters"></div>',
        "results": '<div class="job-list"></div>',
        "hasJobs": False,
        "hasContent": False,
    }


def _synth_page(n_jobs: int, page_offset: int = 0) -> dict[str, Any]:
    """Build a synthetic well-shaped envelope with ``n_jobs`` anchors.

    Each anchor URL is deterministic in ``page_offset`` so multi-page
    tests can assert ordered dedup across pages without collisions.
    """
    hrefs = "".join(
        f'<li><a class="sr-job-item__link" href="/job/city/role-{page_offset + i}'
        f'/287/{100000 + page_offset + i}">Role {page_offset + i}</a></li>'
        for i in range(n_jobs)
    )
    return {
        "filters": '<div class="filters"></div>',
        "results": f'<ul class="job-list">{hrefs}</ul>',
        "hasJobs": True,
        "hasContent": False,
    }


class TestHappyPath:
    """Recorded-payload replay pins the Citi C12 closure end to end."""

    def test_recorded_payload_yields_ten_urls(self) -> None:
        # Page 1: the recorded 10 anchors (== records_per_page → adapter
        # will attempt page 2). Page 2: hasJobs=false terminator, so
        # the union is exactly the 10 recorded URLs.
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_CITI_ENDPOINT).mock(
                side_effect=[
                    httpx.Response(200, json=_load_citi_fixture()),
                    httpx.Response(200, json=_empty_page_payload()),
                ]
            )
            result = _run(TalentbrewStrategy().extract(_make_company(), _ctx()))
        assert result["metadata"]["error"] is None
        assert len(result["jobs"]) == 10

    def test_every_recorded_url_is_absolutized_to_citi_origin(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_CITI_ENDPOINT).mock(
                side_effect=[
                    httpx.Response(200, json=_load_citi_fixture()),
                    httpx.Response(200, json=_empty_page_payload()),
                ]
            )
            result = _run(TalentbrewStrategy().extract(_make_company(), _ctx()))
        for url in result["jobs"]:
            assert url.startswith(f"{_CITI_ORIGIN}/job/"), url

    def test_known_record_pins_a_full_url(self) -> None:
        # The first anchor in the recorded fragment.
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_CITI_ENDPOINT).mock(
                side_effect=[
                    httpx.Response(200, json=_load_citi_fixture()),
                    httpx.Response(200, json=_empty_page_payload()),
                ]
            )
            result = _run(TalentbrewStrategy().extract(_make_company(), _ctx()))
        assert (
            f"{_CITI_ORIGIN}/job/heredia/mca-lead-analyst/287/93146003136"
            in result["jobs"]
        )

    def test_report_carries_agent_fields_as_none(self) -> None:
        # API strategies emit None for the four agent-only fields; the
        # report shape is authored by ``build_report`` and the port
        # test pins the key set — we just pin the None-ness here.
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_CITI_ENDPOINT).mock(
                side_effect=[
                    httpx.Response(200, json=_load_citi_fixture()),
                    httpx.Response(200, json=_empty_page_payload()),
                ]
            )
            result = _run(TalentbrewStrategy().extract(_make_company(), _ctx()))
        md = result["metadata"]
        assert md["model"] is None
        assert md["agent_steps"] is None
        assert md["agent_completed"] is None
        assert md["agent_had_errors"] is None
        assert md["strategy"] == "talentbrew"

    def test_report_url_is_the_catalog_job_board_url(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_CITI_ENDPOINT).mock(
                side_effect=[
                    httpx.Response(200, json=_load_citi_fixture()),
                    httpx.Response(200, json=_empty_page_payload()),
                ]
            )
            result = _run(TalentbrewStrategy().extract(_make_company(), _ctx()))
        assert result["url"] == f"{_CITI_ORIGIN}/search-jobs"


class TestPagination:
    """The two-signal continue rule and the union across pages."""

    def test_partial_last_page_terminates_after_one_call(self) -> None:
        # Page 1 yields 3 anchors < records_per_page=10 → break, no
        # page 2 call.
        with respx.mock() as mock:
            route = mock.get(_CITI_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_synth_page(3))
            )
            result = _run(TalentbrewStrategy().extract(_make_company(), _ctx()))
        assert route.call_count == 1
        assert len(result["jobs"]) == 3
        assert result["metadata"]["error"] is None

    def test_multi_page_continuation_unions_urls(self) -> None:
        # Two full pages then a partial page: adapter walks all three
        # and unions 10 + 10 + 3 = 23 distinct URLs.
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_CITI_ENDPOINT).mock(
                side_effect=[
                    httpx.Response(200, json=_synth_page(10, page_offset=0)),
                    httpx.Response(200, json=_synth_page(10, page_offset=100)),
                    httpx.Response(200, json=_synth_page(3, page_offset=200)),
                ]
            )
            result = _run(TalentbrewStrategy().extract(_make_company(), _ctx()))
        assert result["metadata"]["error"] is None
        assert len(result["jobs"]) == 23

    def test_hasjobs_false_on_page_two_is_a_clean_terminator(self) -> None:
        # Page 1 is exactly records_per_page anchors so the adapter
        # would continue; page 2 answering ``hasJobs=false`` is the
        # legitimate terminator, not an error.
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_CITI_ENDPOINT).mock(
                side_effect=[
                    httpx.Response(200, json=_synth_page(10)),
                    httpx.Response(200, json=_empty_page_payload()),
                ]
            )
            result = _run(TalentbrewStrategy().extract(_make_company(), _ctx()))
        assert result["metadata"]["error"] is None
        assert len(result["jobs"]) == 10

    def test_ordered_dedup_across_pages_preserves_first_seen(self) -> None:
        # Page 2 repeats one of page 1's URLs; the duplicate must not
        # appear twice and the surviving URL keeps its page-1 position.
        page1 = _synth_page(10, page_offset=0)
        page2 = _synth_page(10, page_offset=100)
        # Inject a duplicate of the first page-1 anchor into page 2's
        # fragment head.
        duplicate = (
            '<a class="sr-job-item__link" href="/job/city/role-0/287/100000">'
            "Role 0 (dup)</a>"
        )
        page2["results"] = duplicate + page2["results"]
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_CITI_ENDPOINT).mock(
                side_effect=[
                    httpx.Response(200, json=page1),
                    httpx.Response(200, json=page2),
                    httpx.Response(200, json=_empty_page_payload()),
                ]
            )
            result = _run(TalentbrewStrategy().extract(_make_company(), _ctx()))
        # Page 1 emits roles 0..9 (10 unique), page 2 emits the
        # injected duplicate of role-0 plus roles 100..109 (11 anchors,
        # 10 unique-to-this-page + 1 already-seen). Total union = 20.
        assert len(result["jobs"]) == 20
        # And the duplicate URL must appear exactly once — first-seen
        # is on page 1, so it lives at index 0 and nowhere else.
        dup_url = f"{_CITI_ORIGIN}/job/city/role-0/287/100000"
        assert result["jobs"].count(dup_url) == 1
        assert result["jobs"][0] == dup_url

    def test_pagination_increments_the_currentpage_query_param(self) -> None:
        # ``build_query_params`` is unit-tested directly upstream; here
        # we pin that the adapter actually threads ``page`` into it, so
        # a regression that always requested page 1 would fail loudly.
        seen_pages: list[str] = []

        def _capture(request: httpx.Request) -> httpx.Response:
            seen_pages.append(request.url.params["CurrentPage"])
            n = int(request.url.params["CurrentPage"])
            if n <= 2:
                return httpx.Response(200, json=_synth_page(10, page_offset=n * 100))
            return httpx.Response(200, json=_empty_page_payload())

        with respx.mock(assert_all_called=False) as mock:
            mock.get(_CITI_ENDPOINT).mock(side_effect=_capture)
            _run(TalentbrewStrategy().extract(_make_company(), _ctx()))
        assert seen_pages == ["1", "2", "3"]


class TestHasJobsFalseSemantics:
    """The ``hasJobs`` flag drives honest-empty vs. error branches."""

    def test_hasjobs_false_on_page_one_returns_honest_empty(self) -> None:
        # A tenant with zero region-matching postings today. Not an
        # error — the verdict layer classifies the empty result.
        with respx.mock() as mock:
            mock.get(_CITI_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_empty_page_payload())
            )
            result = _run(TalentbrewStrategy().extract(_make_company(), _ctx()))
        assert result["metadata"]["error"] is None
        assert result["jobs"] == []


class TestPageOneLoudGuards:
    """Page-1 shape-drift guards fire loudly rather than emitting empty."""

    def test_page_one_zero_anchors_with_hasjobs_true_is_loud_error(self) -> None:
        payload = _empty_page_payload()
        payload["hasJobs"] = True  # promise jobs but ship empty fragment
        with respx.mock() as mock:
            mock.get(_CITI_ENDPOINT).mock(
                return_value=httpx.Response(200, json=payload)
            )
            result = _run(TalentbrewStrategy().extract(_make_company(), _ctx()))
        assert result["jobs"] == []
        assert result["metadata"]["error"] is not None
        assert "zero anchors" in result["metadata"]["error"]

    def test_page_one_zero_survivors_names_path_prefix_and_min_depth(
        self,
    ) -> None:
        # Point the LinkRule at a path prefix that no anchor matches;
        # the parser sees 10 anchors, LinkRule drops all 10, adapter
        # must raise loudly on page 1.
        company = _make_company(link_rule=LinkRule(path_prefix="/careers", min_depth=3))
        with respx.mock() as mock:
            mock.get(_CITI_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_load_citi_fixture())
            )
            result = _run(TalentbrewStrategy().extract(company, _ctx()))
        assert result["jobs"] == []
        msg = result["metadata"]["error"]
        assert msg is not None
        assert "/careers" in msg
        assert "min_depth=3" in msg


class TestEnvelopeShapeGuards:
    """``results`` must be a string and ``hasJobs`` a bool — loud otherwise."""

    def test_results_not_string_is_error(self) -> None:
        payload = _empty_page_payload()
        payload["results"] = 42  # type: ignore[assignment]
        payload["hasJobs"] = True
        with respx.mock() as mock:
            mock.get(_CITI_ENDPOINT).mock(
                return_value=httpx.Response(200, json=payload)
            )
            result = _run(TalentbrewStrategy().extract(_make_company(), _ctx()))
        assert result["jobs"] == []
        assert "results" in (result["metadata"]["error"] or "")

    def test_hasjobs_not_bool_is_error(self) -> None:
        payload = _empty_page_payload()
        payload["hasJobs"] = "yes"  # type: ignore[assignment]
        with respx.mock() as mock:
            mock.get(_CITI_ENDPOINT).mock(
                return_value=httpx.Response(200, json=payload)
            )
            result = _run(TalentbrewStrategy().extract(_make_company(), _ctx()))
        assert result["jobs"] == []
        assert "hasJobs" in (result["metadata"]["error"] or "")

    def test_non_object_body_is_error(self) -> None:
        with respx.mock() as mock:
            mock.get(_CITI_ENDPOINT).mock(
                return_value=httpx.Response(200, json=["not", "a", "dict"])
            )
            result = _run(TalentbrewStrategy().extract(_make_company(), _ctx()))
        assert result["jobs"] == []
        assert result["metadata"]["error"] is not None


class TestRetryPolicy:
    """One retry on transport errors and 5xx; second failure reports."""

    def test_5xx_then_success(self) -> None:
        # Server error on first attempt, success on retry.
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_CITI_ENDPOINT).mock(
                side_effect=[
                    httpx.Response(503),
                    httpx.Response(200, json=_synth_page(3)),
                ]
            )
            result = _run(TalentbrewStrategy().extract(_make_company(), _ctx()))
        assert result["metadata"]["error"] is None
        assert len(result["jobs"]) == 3

    def test_two_server_errors_produce_an_error_report(self) -> None:
        with respx.mock() as mock:
            mock.get(_CITI_ENDPOINT).mock(
                side_effect=[httpx.Response(503), httpx.Response(503)]
            )
            result = _run(TalentbrewStrategy().extract(_make_company(), _ctx()))
        assert result["jobs"] == []
        assert result["metadata"]["error"] is not None

    def test_transport_error_then_success(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_CITI_ENDPOINT).mock(
                side_effect=[
                    httpx.ConnectError("boom"),
                    httpx.Response(200, json=_synth_page(3)),
                ]
            )
            result = _run(TalentbrewStrategy().extract(_make_company(), _ctx()))
        assert result["metadata"]["error"] is None
        assert len(result["jobs"]) == 3

    def test_two_transport_errors_produce_an_error_report(self) -> None:
        with respx.mock() as mock:
            mock.get(_CITI_ENDPOINT).mock(
                side_effect=[
                    httpx.ConnectError("boom-1"),
                    httpx.ConnectError("boom-2"),
                ]
            )
            result = _run(TalentbrewStrategy().extract(_make_company(), _ctx()))
        assert result["jobs"] == []
        assert result["metadata"]["error"] is not None

    def test_4xx_is_not_retried(self) -> None:
        # 4xx is a client-side contract breakage, not a transient
        # failure — one shot, then error.
        with respx.mock() as mock:
            route = mock.get(_CITI_ENDPOINT).mock(return_value=httpx.Response(404))
            result = _run(TalentbrewStrategy().extract(_make_company(), _ctx()))
        assert route.call_count == 1
        assert result["jobs"] == []
        assert result["metadata"]["error"] is not None


class TestConfigFaults:
    """Missing config surfaces as an error report, not a raised exception."""

    def test_missing_talentbrew_config_is_error(self) -> None:
        # A Company with strategy="talentbrew" and talentbrew=None is
        # blocked by the pydantic validator, so we synthesise one
        # through model_construct which bypasses validation.
        company = _make_company()
        company_no_cfg = Company.model_construct(
            **{**company.model_dump(), "talentbrew": None}
        )
        # No HTTP should be issued; respx will assert on any unmocked
        # call.
        with respx.mock():
            result = _run(TalentbrewStrategy().extract(company_no_cfg, _ctx()))
        assert result["jobs"] == []
        assert "TalentbrewConfig" in (result["metadata"]["error"] or "")

    def test_missing_path_prefix_is_error(self) -> None:
        company = _make_company(link_rule=LinkRule())
        with respx.mock():
            result = _run(TalentbrewStrategy().extract(company, _ctx()))
        assert result["jobs"] == []
        assert "path_prefix" in (result["metadata"]["error"] or "")


class TestMaxPagesCap:
    """The defensive ``_MAX_PAGES`` cap fires a warning and emits union."""

    def test_max_pages_cap_warns_and_emits_union(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Every page returns a full ``records_per_page`` batch of
        # distinct anchors, so the adapter never breaks and hits the
        # cap. It must emit the 20-page union and log a warning naming
        # the cap.
        def _always_full(request: httpx.Request) -> httpx.Response:
            n = int(request.url.params["CurrentPage"])
            return httpx.Response(200, json=_synth_page(10, page_offset=n * 100))

        _logger = "vacantes.extraction.ats.talentbrew"
        with (
            respx.mock() as mock,
            caplog.at_level(logging.WARNING, logger=_logger),
        ):
            mock.get(_CITI_ENDPOINT).mock(side_effect=_always_full)
            result = _run(TalentbrewStrategy().extract(_make_company(), _ctx()))
        assert result["metadata"]["error"] is None
        assert len(result["jobs"]) == 200  # 20 pages × 10 unique
        assert any(
            "_MAX_PAGES" in rec.message and "20" in rec.message
            for rec in caplog.records
        )


class TestQueryContract:
    """The wire contract carries the facet and the paging index."""

    def test_query_carries_facet_and_page_params(self) -> None:
        captured: list[httpx.URL] = []

        def _capture(request: httpx.Request) -> httpx.Response:
            captured.append(request.url)
            return httpx.Response(200, json=_synth_page(3))

        with respx.mock() as mock:
            mock.get(_CITI_ENDPOINT).mock(side_effect=_capture)
            _run(TalentbrewStrategy().extract(_make_company(), _ctx()))
        assert len(captured) == 1
        url = captured[0]
        # Facet id + display are the tenant literals from
        # TalentbrewConfig; page indexing is 1-based.
        assert url.params["FacetFilters[0].ID"] == "3624060"
        assert url.params["FacetFilters[0].Display"] == "Costa Rica"
        assert url.params["CurrentPage"] == "1"

    def test_no_csrf_or_referer_headers_are_required(self) -> None:
        # The adapter must not send x-csrf-token / referer / cookie
        # headers of its own — the recorded probe on 2026-08-04 showed
        # the endpoint answers without them.
        captured: list[httpx.Headers] = []

        def _capture(request: httpx.Request) -> httpx.Response:
            captured.append(request.headers)
            return httpx.Response(200, json=_synth_page(3))

        with respx.mock() as mock:
            mock.get(_CITI_ENDPOINT).mock(side_effect=_capture)
            _run(TalentbrewStrategy().extract(_make_company(), _ctx()))
        assert len(captured) == 1
        headers = captured[0]
        assert "x-csrf-token" not in {k.lower() for k in headers}
        # httpx sets no cookie unless explicitly configured.
        assert "cookie" not in {k.lower() for k in headers}


class TestNeverRaises:
    """Every failure path is caught and folded into an error report."""

    def test_generic_exception_is_absorbed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Inject a helper failure well inside the extract() try/except
        # to prove no exception escapes the strategy.
        from vacantes.extraction.ats import talentbrew as tb

        def _boom(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("synthetic downstream failure")

        monkeypatch.setattr(tb, "build_query_params", _boom)
        result = _run(TalentbrewStrategy().extract(_make_company(), _ctx()))
        assert result["jobs"] == []
        assert "synthetic downstream failure" in (result["metadata"]["error"] or "")


_VEEAM_ORIGIN = "https://careers.veeam.com"
_VEEAM_ENDPOINT = f"{_VEEAM_ORIGIN}/search-jobs/results"


def _load_veeam_fixture() -> dict[str, Any]:
    """Read the recorded Veeam ``/search-jobs/results`` payload."""
    payload = json.loads((_FIXTURE_DIR / "veeam.json").read_text())
    assert isinstance(payload, dict), "veeam.json is not a JSON object"
    return payload


def _make_veeam_company(**overrides: Any) -> Company:
    """Build the shipped ``Veeam Software`` catalog entry's shape."""
    defaults: dict[str, Any] = {
        "name": "Veeam Software",
        "job_board_url": f"{_VEEAM_ORIGIN}/search-jobs",
        "sample_job_url": f"{_VEEAM_ORIGIN}/job/-/-/22681/97474976224",
        "strategy": "talentbrew",
        "talentbrew": TalentbrewConfig(
            facet_id="3624060",
            facet_display="Costa Rica",
        ),
        "link_rule": LinkRule(path_prefix="/job"),
        "expected_jobs": 11,
    }
    defaults.update(overrides)
    return Company(**defaults)


class TestVeeamRecordedPayload:
    """Second Talentbrew tenant, pinned against its recorded payload.

    Veeam matters beyond "one more tenant" for two reasons. First, its
    Costa Rica set is 11 against the schema-default
    ``records_per_page=15``, so the adapter's continue-paginating rule
    (advance iff the filtered count equals the page size) terminates on
    page 1 — the single-request path Citi's fixture cannot exercise,
    because Citi is pinned to ``records_per_page=10`` with exactly 10
    recorded anchors and therefore always probes a second page.

    Second, it pins a real cross-tenant fact: ``facet_id="3624060"`` is
    byte-identical to Citi's. Talentbrew keys its country facets on
    GeoNames ids (3624060 *is* GeoNames' Costa Rica), so the id is
    platform-global rather than tenant-specific — worth having a test
    assert, since the config docstring presents it as a per-tenant
    value read off a capture.
    """

    def test_recorded_payload_yields_eleven_urls(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_VEEAM_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_load_veeam_fixture())
            )
            result = _run(TalentbrewStrategy().extract(_make_veeam_company(), _ctx()))
        assert result["metadata"]["error"] is None
        assert len(result["jobs"]) == 11

    def test_single_request_when_count_below_page_size(self) -> None:
        # 11 filtered anchors < records_per_page=15, so the adapter must
        # not probe page 2. Exactly one call proves the termination rule.
        with respx.mock(assert_all_called=False) as mock:
            route = mock.get(_VEEAM_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_load_veeam_fixture())
            )
            _run(TalentbrewStrategy().extract(_make_veeam_company(), _ctx()))
        assert route.call_count == 1

    def test_every_url_is_absolutized_to_veeam_origin(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_VEEAM_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_load_veeam_fixture())
            )
            result = _run(TalentbrewStrategy().extract(_make_veeam_company(), _ctx()))
        for url in result["jobs"]:
            assert url.startswith(f"{_VEEAM_ORIGIN}/job/"), url

    def test_san_jose_slug_is_not_a_region_discriminator(self) -> None:
        # /job/san-jose/ is ambiguous on this board: the facet-filtered
        # set carries San Jose *Costa Rica* postings under it, while the
        # unfiltered board also serves San Jose *CA* postings under the
        # same slug. The facet is what separates them, so the recorded
        # payload must contain san-jose anchors AND the adapter must
        # emit them — a future "filter out san-jose" hack would break
        # this and take 8 real postings with it.
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_VEEAM_ENDPOINT).mock(
                return_value=httpx.Response(200, json=_load_veeam_fixture())
            )
            result = _run(TalentbrewStrategy().extract(_make_veeam_company(), _ctx()))
        san_jose = [u for u in result["jobs"] if "/job/san-jose/" in u]
        assert len(san_jose) == 8

    def test_facet_id_is_shared_with_citi(self) -> None:
        # GeoNames-derived, therefore platform-global rather than
        # tenant-specific. See the class docstring.
        assert (
            _make_veeam_company().talentbrew is not None
            and _make_company().talentbrew is not None
        )
        veeam_cfg = _make_veeam_company().talentbrew
        citi_cfg = _make_company().talentbrew
        assert veeam_cfg is not None and citi_cfg is not None
        assert veeam_cfg.facet_id == citi_cfg.facet_id == "3624060"


_MOODYS_ORIGIN = "https://careers.moodys.com"
_MOODYS_ENDPOINT = f"{_MOODYS_ORIGIN}/en/search-jobs/results"


def _load_moodys_fixture(page: int = 1) -> dict[str, Any]:
    """Read a recorded Moody's ``/en/search-jobs/results`` page payload."""
    name = "moodys.json" if page == 1 else f"moodys_page{page}.json"
    payload = json.loads((_FIXTURE_DIR / name).read_text())
    assert isinstance(payload, dict), f"{name} is not a JSON object"
    return payload


def _make_moodys_company(**overrides: Any) -> Company:
    """Build the shipped ``Moody's Corporation`` catalog entry's shape."""
    defaults: dict[str, Any] = {
        "name": "Moody's Corporation",
        "job_board_url": f"{_MOODYS_ORIGIN}/en/search-jobs",
        "sample_job_url": (
            f"{_MOODYS_ORIGIN}/en/job/heredia/"
            "fin-rptg-and-acct-policy-accountant/49841/99229266848"
        ),
        "strategy": "talentbrew",
        "talentbrew": TalentbrewConfig(
            facet_id="3624060",
            facet_display="Costa Rica",
            results_path="/en/search-jobs/results",
        ),
        "link_rule": LinkRule(path_prefix="/en/job"),
        "expected_jobs": 27,
    }
    defaults.update(overrides)
    return Company(**defaults)


class TestMoodysRecordedPayload:
    """Third Talentbrew tenant — the first that genuinely spans two pages.

    Moody's closes two gaps the Citi and Veeam fixtures leave open.

    First, ``results_path``. Citi and Veeam both serve the widget at the
    schema default ``/search-jobs/results``; Moody's is locale-prefixed
    (``/en/search-jobs/results``), so this is the first entry to exercise
    the override as a real tenant value rather than a synthetic one. The
    same ``/en`` prefix reaches the anchors, which is why the LinkRule is
    ``/en/job`` and not Citi's ``/job``.

    Second, and more load-bearing: ``build_query_params`` documents that
    ``IsPagination=False`` is sent verbatim even for pages >= 2, and that
    the toggle "has never been live-verified" because Citi's Costa Rica
    set fits on one page — naming a future multi-page tenant as the
    trigger to check. Moody's is that tenant: 27 Costa Rica postings
    against ``records_per_page=15`` means page 1 returns a full 15
    (== page size, so the adapter advances) and page 2 returns the
    remaining 12 (< page size, so it stops). A live run on 2026-08-31
    returned 27, so the constant ``IsPagination=False`` is now confirmed
    to work across the page boundary rather than merely assumed. The two
    recorded payloads pin that union offline.
    """

    def test_recorded_payload_yields_twenty_seven_urls(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_MOODYS_ENDPOINT).mock(
                side_effect=[
                    httpx.Response(200, json=_load_moodys_fixture(1)),
                    httpx.Response(200, json=_load_moodys_fixture(2)),
                ]
            )
            result = _run(TalentbrewStrategy().extract(_make_moodys_company(), _ctx()))
        assert result["metadata"]["error"] is None
        assert len(result["jobs"]) == 27

    def test_two_requests_when_first_page_is_full(self) -> None:
        # 15 filtered anchors == records_per_page, so the adapter MUST
        # probe page 2; 12 < 15 terminates it there. Exactly two calls
        # is the multi-page contract Citi/Veeam cannot demonstrate.
        with respx.mock(assert_all_called=False) as mock:
            route = mock.get(_MOODYS_ENDPOINT).mock(
                side_effect=[
                    httpx.Response(200, json=_load_moodys_fixture(1)),
                    httpx.Response(200, json=_load_moodys_fixture(2)),
                ]
            )
            _run(TalentbrewStrategy().extract(_make_moodys_company(), _ctx()))
        assert route.call_count == 2

    def test_every_url_is_absolutized_under_the_en_locale_prefix(self) -> None:
        # The locale prefix is part of the anchor path, not just the
        # results path — a regression that drops it would still produce
        # 27 URLs, all of them 404s.
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_MOODYS_ENDPOINT).mock(
                side_effect=[
                    httpx.Response(200, json=_load_moodys_fixture(1)),
                    httpx.Response(200, json=_load_moodys_fixture(2)),
                ]
            )
            result = _run(TalentbrewStrategy().extract(_make_moodys_company(), _ctx()))
        for url in result["jobs"]:
            assert url.startswith(f"{_MOODYS_ORIGIN}/en/job/"), url

    def test_results_path_override_is_the_requested_endpoint(self) -> None:
        # Guards the override itself: if the adapter ever fell back to
        # the schema default ``/search-jobs/results``, this route would
        # not match and the call count would be zero.
        with respx.mock(assert_all_called=False) as mock:
            route = mock.get(_MOODYS_ENDPOINT).mock(
                side_effect=[
                    httpx.Response(200, json=_load_moodys_fixture(1)),
                    httpx.Response(200, json=_load_moodys_fixture(2)),
                ]
            )
            _run(TalentbrewStrategy().extract(_make_moodys_company(), _ctx()))
        assert route.call_count == 2


_HERAEUS_ORIGIN = "https://jobs.heraeus.com"
_HERAEUS_ENDPOINT = f"{_HERAEUS_ORIGIN}/en/search-jobs/results"


def _load_heraeus_fixture(page: int = 1) -> dict[str, Any]:
    """Read a recorded Heraeus ``/en/search-jobs/results`` page payload."""
    name = "heraeus.json" if page == 1 else f"heraeus_page{page}.json"
    payload = json.loads((_FIXTURE_DIR / name).read_text())
    assert isinstance(payload, dict), f"{name} is not a JSON object"
    return payload


def _make_heraeus_company(**overrides: Any) -> Company:
    """Build the shipped ``Heraeus`` catalog entry's shape."""
    defaults: dict[str, Any] = {
        "name": "Heraeus",
        "job_board_url": f"{_HERAEUS_ORIGIN}/en/search-jobs",
        "sample_job_url": (
            f"{_HERAEUS_ORIGIN}/en/job/cartago/quality-engineer-ii/3105/35497250368"
        ),
        "strategy": "talentbrew",
        "talentbrew": TalentbrewConfig(
            facet_id="3624060",
            facet_display="Costa Rica",
            results_path="/en/search-jobs/results",
        ),
        "link_rule": LinkRule(path_prefix="/en/job"),
        "expected_jobs": 27,
    }
    defaults.update(overrides)
    return Company(**defaults)


class TestHeraeusRecordedPayload:
    """Fourth Talentbrew tenant — the second that spans two pages.

    Heraeus shares Moody's locale-prefixed shape (``/en/search-jobs/
    results`` with ``/en/job`` anchors) and its 27-posting Costa Rica
    facet, so on its own it would only re-cover ground
    ``TestMoodysRecordedPayload`` already pins. What makes it worth a
    fixture is the *pager anchors* in its ``results`` fragment: page 1
    carries 18 raw anchors — the 15 postings plus two
    ``/search-jobs/results&p=N`` cursor links and a bare ``#`` — which
    LinkRule drops to exactly 15. That is the concrete instance of the
    two-signal continuation predicate ``build_query_params``' docstring
    describes in the abstract: a raw count perturbed above the page size
    by chrome, where only the *filtered* count may be compared against
    ``records_per_page``. A regression that compared raw anchors instead
    would read 18 != 15 and stop after page 1, returning 15 of 27.
    """

    def test_recorded_payload_yields_twenty_seven_urls(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_HERAEUS_ENDPOINT).mock(
                side_effect=[
                    httpx.Response(200, json=_load_heraeus_fixture(1)),
                    httpx.Response(200, json=_load_heraeus_fixture(2)),
                ]
            )
            result = _run(TalentbrewStrategy().extract(_make_heraeus_company(), _ctx()))
        assert result["metadata"]["error"] is None
        assert len(result["jobs"]) == 27

    def test_page_one_chrome_anchors_do_not_inflate_the_page_size_check(self) -> None:
        # The fragment's 18 raw anchors exceed records_per_page=15 while
        # the LinkRule survivors equal it exactly. Both facts must hold
        # for this fixture to be the regression guard it claims to be:
        # if a future recording lost the pager links, the test would
        # still pass but stop covering the predicate.
        payload = _load_heraeus_fixture(1)
        raw = parse_anchor_hrefs(payload["results"])
        filtered = apply_link_rule(
            [urljoin(_HERAEUS_ENDPOINT, href) for href in raw],
            origin=_HERAEUS_ORIGIN,
            base_path="/en/job",
        )
        assert len(raw) > 15, "pager/chrome anchors missing from the recording"
        assert len(filtered) == 15

    def test_two_requests_when_first_page_is_full(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            route = mock.get(_HERAEUS_ENDPOINT).mock(
                side_effect=[
                    httpx.Response(200, json=_load_heraeus_fixture(1)),
                    httpx.Response(200, json=_load_heraeus_fixture(2)),
                ]
            )
            _run(TalentbrewStrategy().extract(_make_heraeus_company(), _ctx()))
        assert route.call_count == 2

    def test_every_url_is_absolutized_under_the_en_locale_prefix(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_HERAEUS_ENDPOINT).mock(
                side_effect=[
                    httpx.Response(200, json=_load_heraeus_fixture(1)),
                    httpx.Response(200, json=_load_heraeus_fixture(2)),
                ]
            )
            result = _run(TalentbrewStrategy().extract(_make_heraeus_company(), _ctx()))
        for url in result["jobs"]:
            assert url.startswith(f"{_HERAEUS_ORIGIN}/en/job/"), url

    def test_no_pager_cursor_urls_survive_the_link_rule(self) -> None:
        # ``/search-jobs/results&p=2`` is same-origin and would sail
        # through an origin-only filter. Naming it explicitly documents
        # why the LinkRule is load-bearing on this tenant.
        with respx.mock(assert_all_called=False) as mock:
            mock.get(_HERAEUS_ENDPOINT).mock(
                side_effect=[
                    httpx.Response(200, json=_load_heraeus_fixture(1)),
                    httpx.Response(200, json=_load_heraeus_fixture(2)),
                ]
            )
            result = _run(TalentbrewStrategy().extract(_make_heraeus_company(), _ctx()))
        assert not [url for url in result["jobs"] if "search-jobs" in url]
