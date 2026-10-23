from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from job_pipeline.identity import fingerprint, url_key

FIXTURE = Path(__file__).parents[1] / "fixtures" / "identity" / "snapshot_urls.tsv"


def _snapshot() -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        slug, url = line.split("\t", 1)
        rows.append((slug, url))
    return rows


def test_snapshot_has_one_expected_merge() -> None:
    rows = _snapshot()
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for slug, url in rows:
        grouped[(slug, url_key(url))].append(url)
    duplicates = {key: urls for key, urls in grouped.items() if len(urls) > 1}
    assert len(rows) == 1504
    assert len(grouped) == 1503
    assert duplicates == {
        ("excel_nearshore", "https://excelnearshore.com/job/qa-engineer"): [
            "https://excelnearshore.com/job/qa-engineer/",
            "https://excelnearshore.com/job/qa-engineer/?export-pdf=15047",
        ]
    }


def test_accenture_ids_remain_distinct() -> None:
    urls = [url for slug, url in _snapshot() if slug == "accenture"]
    ids = [parse_qs(urlsplit(url).query)["id"][0] for url in urls]
    assert len(ids) == len(set(ids))
    assert len({url_key(url) for url in urls}) == len(ids)


def test_query_order_fragment_encoding_ports_and_trailing_slash_normalize() -> None:
    first = "HTTPS://Example.COM:443/jobs/x/?b=hello%20world&a=2&a=1&utm_x=z#part"
    second = "https://example.com/jobs/x?a=1&b=hello+world&a=2"
    assert url_key(first) == url_key(second)
    assert url_key(first) == "https://example.com/jobs/x?a=1&a=2&b=hello+world"
    assert url_key("http://EXAMPLE.com:8080/") == "http://example.com:8080/"


@pytest.mark.parametrize(
    "url", ["/jobs/1", "ftp://example.com/jobs/1", "https://x:bad/"]
)
def test_malformed_or_unsupported_urls_are_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        url_key(url)


def test_fingerprint_is_case_and_whitespace_stable_and_prefix_bounded() -> None:
    baseline = fingerprint(" Senior Engineer ", "Build APIs\nwith Python")
    assert baseline == fingerprint("SENIOR engineer", "Build   APIs with python")
    assert fingerprint("Role", "a" * 2000 + "x") == fingerprint(
        "role", "a" * 2000 + "y"
    )
    assert baseline != fingerprint("Different", "Build APIs with Python")
