"""The posting-page guard behind the agent's ``extract_job_links`` tool.

The matcher reports whatever anchors are on the page it is handed and
has no notion of *which* page that is. On Deloitte the agent wandered
into a posting's detail page on one run in three and called the tool
there; the matcher truthfully found the one job link on that page and
returned 1 — a number nothing downstream could tell from a small board,
and one a batch run would have persisted by deleting the 13 real URLs.

:func:`~vacantes.extraction.dom.rules.is_posting_url` closes that by
asking whether the *current page's own URL* would be collected as a
posting by the matcher's id-in-path rule. Three groups of tests pin it:

- The predicate itself, including the two deliberate exclusions — the
  id-in-query shape (a filtered listing root looks exactly like a
  query-keyed posting) and the board's own listing path (Team Talent's
  listing sits under its own prefix at posting depth).
- Parity with the already-parity-tested Python mirror of the JS matcher
  in ``ats/talentbrew.py``, so the guard cannot drift from the matcher
  about what a posting looks like without this file noticing.
- Corpus-wide safety: no catalog listing URL, and no recorded pagination
  or pre-filter state URL in any snapshot, trips the guard. A future
  entry whose listing does will fail here rather than live.

Plus one integration test that the registered tool actually refuses on a
posting URL with an ``error`` the agent can act on, and proceeds to the
matcher on a listing URL.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

from tests.unit.asyncio_harness import run_async
from vacantes.catalog import COMPANIES
from vacantes.extraction.ats.talentbrew import apply_link_rule
from vacantes.extraction.dom.agent import controller as controller_module
from vacantes.extraction.dom.agent.controller import build_controller
from vacantes.extraction.dom.rules import derive_path_prefix, is_posting_url

_SNAPSHOTS = Path(__file__).resolve().parent.parent / "fixtures" / "snapshots"


class TestIsPostingUrl:
    """The predicate, case by case, each anchored to a real corpus board."""

    # Deloitte: the motivating board. Listing /search/, postings /job/<slug>/<id>/.
    _DEL: dict[str, Any] = {
        "origin": "https://carrera.deloitteslatam.com",
        "base_path": "/job",
        "min_depth": 1,
        "listing_url": "https://carrera.deloitteslatam.com/search/?q=&locationsearch=costa+rica",
    }

    def test_a_posting_detail_page_is_a_posting(self) -> None:
        url = "https://carrera.deloitteslatam.com/job/Costa-Rica-Consultor-DevOps/1377581033/"
        assert is_posting_url(url, **self._DEL) is True

    def test_the_listing_page_is_not(self) -> None:
        assert is_posting_url(self._DEL["listing_url"], **self._DEL) is False

    def test_an_agent_applied_filter_query_does_not_defeat_the_listing_exemption(
        self,
    ) -> None:
        # The agent may change the query while filtering; the path is what
        # identifies the listing.
        url = "https://carrera.deloitteslatam.com/search/?createNewAlert=true&q=devops&locationsearch=costa+rica"
        assert is_posting_url(url, **self._DEL) is False

    def test_query_shape_listing_root_is_not_a_posting(self) -> None:
        # Sparq (Rippling): listing and prefix share a path; the filter is a
        # query. The matcher would bucket this as id-in-query; the guard must
        # not — 28 corpus listings have exactly this form.
        assert (
            is_posting_url(
                "https://ats.rippling.com/en-GB/sparq/jobs?country=CR",
                origin="https://ats.rippling.com",
                base_path="/en-GB/sparq/jobs",
                listing_url="https://ats.rippling.com/en-GB/sparq/jobs?country=CR",
            )
            is False
        )

    def test_but_a_path_shape_posting_on_that_board_is(self) -> None:
        assert (
            is_posting_url(
                "https://ats.rippling.com/en-GB/sparq/jobs/808bb533-70c8-40a7-86c5-5fdd6d42c31f",
                origin="https://ats.rippling.com",
                base_path="/en-GB/sparq/jobs",
                listing_url="https://ats.rippling.com/en-GB/sparq/jobs?country=CR",
            )
            is True
        )

    def test_team_talent_listing_under_its_own_prefix_is_exempt(self) -> None:
        # The one corpus board whose listing path itself buckets as a
        # posting under the prefix. Without the listing exemption the guard
        # would refuse to extract on a working board.
        kw: dict[str, Any] = {
            "origin": "https://teaminternational1234.my.site.com",
            "base_path": "/teamsites",
            "listing_url": "https://teaminternational1234.my.site.com/teamsites/our-openings",
        }
        assert is_posting_url(kw["listing_url"], **kw) is False
        # ...while its real postings still trip it.
        assert (
            is_posting_url(
                "https://teaminternational1234.my.site.com/teamsites/job-posting?id=a13UV",
                **kw,
            )
            is True
        )

    def test_min_depth_is_honoured(self) -> None:
        # J&J: chrome at depth 1 under /en/jobs, postings at depth 2.
        kw: dict[str, Any] = {
            "origin": "https://www.careers.jnj.com",
            "base_path": "/en/jobs",
            "min_depth": 2,
            "listing_url": "https://www.careers.jnj.com/en/jobs/?country=Costa%20Rica",
        }
        assert (
            is_posting_url(
                "https://www.careers.jnj.com/en/jobs/r-078067/sr-data-engineer/", **kw
            )
            is True
        )
        assert (
            is_posting_url("https://www.careers.jnj.com/en/jobs/some-chrome-page", **kw)
            is False
        )

    def test_trailing_slash_strips_exactly_one(self) -> None:
        # Mirrors JS ``.replace(/\/$/, '')``: one slash, not a run.
        kw: dict[str, Any] = {
            "origin": "https://x.com",
            "base_path": "/jobs",
            "listing_url": "https://x.com/careers",
        }
        assert is_posting_url("https://x.com/jobs/123/", **kw) is True
        assert is_posting_url("https://x.com/jobs/123", **kw) is True
        # ``/jobs//`` strips to ``/jobs/`` -> remainder "" -> one empty
        # segment -> depth 1 -> still counted, exactly as the JS does.
        assert is_posting_url("https://x.com/jobs//", **kw) is True

    def test_other_origin_is_never_a_posting_of_this_board(self) -> None:
        assert (
            is_posting_url(
                "https://elsewhere.example/job/123",
                origin="https://carrera.deloitteslatam.com",
                base_path="/job",
                listing_url="https://carrera.deloitteslatam.com/search/",
            )
            is False
        )

    def test_root_prefix_never_fires(self) -> None:
        # base_path "/" -> startswith("//") is false for any real path; the
        # same quirk the matcher has, preserved rather than special-cased.
        assert (
            is_posting_url(
                "https://x.com/anything/at/all",
                origin="https://x.com",
                base_path="/",
                listing_url="https://x.com/",
            )
            is False
        )

    @pytest.mark.parametrize("bad", ["", "not a url", "relative/path/1", "://nohost"])
    def test_unparseable_or_relative_is_false_not_an_exception(self, bad: str) -> None:
        assert (
            is_posting_url(
                bad,
                origin="https://x.com",
                base_path="/jobs",
                listing_url="https://x.com/",
            )
            is False
        )


class TestParityWithTheMatcherMirror:
    """The guard's notion of "posting" is the matcher's id-in-path bucket.

    ``apply_link_rule`` is the parity-tested Python mirror of the JS
    matcher. For any URL that is not query-shaped and not the listing,
    the guard must agree with it exactly — otherwise the guard could
    refuse a page the matcher would happily have read, or wave through
    a page the matcher would call a posting.
    """

    _CASES = [
        ("https://x.com/jobs/123", "/jobs", 1),
        ("https://x.com/jobs/123/", "/jobs", 1),
        ("https://x.com/jobs/a/b", "/jobs", 2),
        ("https://x.com/jobs/a", "/jobs", 2),  # too shallow for min_depth=2
        ("https://x.com/job/Slug/1377581033/", "/job", 1),
        ("https://x.com/en-US/External/job/City/Title_REQ1", "/en-US/External/job", 1),
        ("https://x.com/jobsx/123", "/jobs", 1),  # prefix must be a segment boundary
        ("https://x.com/jobs", "/jobs", 1),  # equals prefix, no query -> neither bucket
    ]

    @pytest.mark.parametrize("url,base_path,min_depth", _CASES)
    def test_agrees_with_apply_link_rule(
        self, url: str, base_path: str, min_depth: int
    ) -> None:
        mirror_says_posting = bool(
            apply_link_rule(
                [url], origin="https://x.com", base_path=base_path, min_depth=min_depth
            )
        )
        guard_says_posting = is_posting_url(
            url,
            origin="https://x.com",
            base_path=base_path,
            min_depth=min_depth,
            listing_url="https://x.com/definitely-not-this-path",
        )
        assert guard_says_posting == mirror_says_posting, url


class TestCorpusSafety:
    """No listing URL anywhere in the corpus may trip the guard.

    Two sources: every DOM entry's ``job_board_url`` and
    ``pre_filter_urls``, and every URL recorded in a snapshot's
    ``top_url`` / ``pages`` / ``states`` — i.e. every page the walker has
    ever legitimately been on. A hit here is a board whose listing the
    guard would refuse; the fix is the listing exemption widening, never
    the entry silently shipping.
    """

    @staticmethod
    def _effective(c: Any) -> tuple[str, str, int]:
        prefix = c.link_rule.path_prefix or derive_path_prefix(c.sample_job_url)
        p = urlparse(c.job_board_url)
        return f"{p.scheme}://{p.netloc}", prefix, c.link_rule.min_depth

    def test_no_catalog_listing_url_is_a_posting(self) -> None:
        hits = []
        for c in COMPANIES:
            if c.strategy != "dom":
                continue
            origin, prefix, depth = self._effective(c)
            for url in (c.job_board_url, *c.pre_filter_urls):
                if is_posting_url(
                    url,
                    origin=origin,
                    base_path=prefix,
                    min_depth=depth,
                    listing_url=c.job_board_url,
                ):
                    hits.append((c.name, url))
        assert hits == [], f"guard would refuse these LISTING urls: {hits}"

    def test_no_recorded_pagination_or_state_url_is_a_posting(self) -> None:
        by_slug = {c.slug: c for c in COMPANIES if c.strategy == "dom"}
        hits, checked = [], 0
        for meta_path in _SNAPSHOTS.glob("*/metadata.json"):
            c = by_slug.get(meta_path.parent.name)
            if c is None:
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            origin, prefix, depth = self._effective(c)
            urls = [meta.get("top_url") or c.job_board_url]
            urls += [p["url"] for p in meta.get("pages", [])]
            for st in meta.get("states", []):
                urls.append(st["url"])
                urls += [p["url"] for p in st.get("pages", [])]
            for url in urls:
                checked += 1
                if is_posting_url(
                    url,
                    origin=origin,
                    base_path=prefix,
                    min_depth=depth,
                    listing_url=c.job_board_url,
                ):
                    hits.append((c.name, url))
        assert checked > 100, (
            "snapshot corpus unexpectedly small; is the fixtures dir wired?"
        )
        assert hits == [], f"guard would refuse these recorded listing states: {hits}"


class _FakePage:
    def __init__(self, url: str) -> None:
        self._url = url

    async def get_url(self) -> str:
        return self._url


class _FakeSession:
    def __init__(self, url: str) -> None:
        self._page = _FakePage(url)

    async def get_current_page(self) -> _FakePage:
        return self._page


class TestExtractJobLinksToolGuard:
    """The registered tool refuses on a posting and proceeds on a listing."""

    _BOARD = "https://carrera.deloitteslatam.com/search/?q=&locationsearch=costa+rica"
    _SAMPLE = "https://carrera.deloitteslatam.com/job/Costa-Rica-Consultor/1377581033/"
    _POSTING = "https://carrera.deloitteslatam.com/job/Costa-Rica-Gerente-Business-Tax/1377581099/"

    def _tool(self) -> Any:
        controller = build_controller(self._BOARD, self._SAMPLE, path_prefix="/job")
        return controller.registry.registry.actions["extract_job_links"].function

    def test_refuses_on_a_posting_page_with_an_actionable_error(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        called: list[Any] = []

        async def _never(*a: Any, **k: Any) -> list[str]:
            called.append(a)
            return ["should-not-happen"]

        monkeypatch.setattr(controller_module, "collect_job_links", _never)
        with caplog.at_level(logging.WARNING, logger=controller_module.__name__):
            result = run_async(
                self._tool()(browser_session=_FakeSession(self._POSTING))
            )
        # A batch operator reads logs, not ActionResults: the refusal must
        # surface there too, naming the page and the listing to return to.
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("refused on a posting page" in r.getMessage() for r in warnings)
        assert any(self._POSTING in r.getMessage() for r in warnings)
        assert result.error, "expected a refusal error"
        assert "job posting" in result.error and "listings page" in result.error
        assert self._BOARD in result.error, "the error must tell the agent where to go"
        assert result.is_done is False, "a refusal must not end the run"
        assert result.extracted_content is None
        assert called == [], "the matcher must not run on a refused page"

    def test_proceeds_to_the_matcher_on_the_listing_page(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _fake_collect(*a: Any, **k: Any) -> list[str]:
            return [self._POSTING, self._SAMPLE]

        monkeypatch.setattr(controller_module, "collect_job_links", _fake_collect)
        result = run_async(self._tool()(browser_session=_FakeSession(self._BOARD)))
        assert result.error is None
        assert result.is_done is True
        assert json.loads(result.extracted_content or "{}") == {
            "jobs": [self._POSTING, self._SAMPLE]
        }

    def test_a_filtered_listing_url_still_proceeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The agent changed the query while filtering; still the listing.
        async def _fake_collect(*a: Any, **k: Any) -> list[str]:
            return []

        monkeypatch.setattr(controller_module, "collect_job_links", _fake_collect)
        filtered = "https://carrera.deloitteslatam.com/search/?createNewAlert=false&q=tax&locationsearch=costa+rica"
        result = run_async(self._tool()(browser_session=_FakeSession(filtered)))
        assert result.error is None and result.is_done is True
