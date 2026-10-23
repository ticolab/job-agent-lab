from __future__ import annotations

from datetime import date

from job_pipeline.content.jsonld import extract_job_postings


def test_parses_arrays_graph_types_html_and_metadata() -> None:
    html = """
    <script type="application/ld+json">not json</script>
    <script type="application/ld+json">
    {"@graph": [{"@type": "WebPage"}, {"@type": ["Thing", "JobPosting"],
      "title": "Senior Engineer",
      "description": "<h2>Role</h2><ul><li>Build APIs</li></ul>",
      "identifier": {"value": "REQ-7"}, "datePosted": "2026-10-01",
      "validThrough": "2026-12-01T23:59:59Z", "employmentType": ["FULL_TIME"],
      "hiringOrganization": {"name": "Acme"},
      "jobLocation": [{"address": {
        "addressLocality": "Heredia", "addressCountry": "CR"}}]}]}
    </script>
    """
    postings = extract_job_postings(html)
    assert len(postings) == 1
    posting = postings[0]
    assert posting.title == "Senior Engineer"
    assert "Role" in posting.description_text
    assert "Build APIs" in posting.description_text
    assert posting.identifier == "REQ-7"
    assert posting.locations == ("Heredia, CR",)
    assert posting.employment_type == ("FULL_TIME",)
    assert posting.hiring_org == "Acme"
    assert posting.is_closed(today=date(2026, 10, 23)) is False


def test_past_valid_through_is_closed_and_malformed_date_is_not() -> None:
    past = extract_job_postings(
        '<script type="application/ld+json">'
        '{"@type":"JobPosting","validThrough":"2020-01-01"}'
        "</script>"
    )[0]
    malformed = extract_job_postings(
        '<script type="application/ld+json">'
        '{"@type":"JobPosting","validThrough":"soon"}'
        "</script>"
    )[0]
    assert past.is_closed(today=date(2026, 10, 23)) is True
    assert malformed.is_closed(today=date(2026, 10, 23)) is False


def test_page_without_jsonld_has_no_postings() -> None:
    assert extract_job_postings("<html><body>No structured data</body></html>") == ()
