from __future__ import annotations

from job_pipeline.content.text import looks_closed, main_text, normalize


def test_normalize_preserves_paragraphs_and_normalizes_unicode() -> None:
    assert normalize(" Cafe\u0301   role\r\n\r\n\r\n  Build\tAPIs ") == (
        "Café role\n\nBuild APIs"
    )


def test_main_text_extracts_title_body_and_list_items() -> None:
    html = """
    <html><head><title>Senior Engineer | Acme</title></head><body><main>
    <h1>Senior Engineer</h1><p>Join our platform team and build reliable systems.</p>
    <ul><li>Build APIs</li><li>Review code</li></ul>
    </main></body></html>
    """
    text, title = main_text(html, "https://example.com/jobs/1")
    assert title == "Senior Engineer | Acme"
    assert "Senior Engineer" in text
    assert "• Build APIs" in text
    assert "• Review code" in text


def test_closed_phrases_are_case_insensitive_and_include_spanish() -> None:
    assert looks_closed("THIS POSITION HAS BEEN FILLED")
    assert looks_closed("Este empleo ya no está disponible.")
    assert not looks_closed("Applications remain open.")
