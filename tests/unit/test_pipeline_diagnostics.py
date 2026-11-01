from __future__ import annotations

import io
import logging
from collections.abc import Iterator

import pytest

from job_pipeline.runner.diagnostics import (
    configure_logging,
    redact_message,
    redact_url,
)


@pytest.fixture
def pipeline_logger() -> Iterator[logging.Logger]:
    logger = logging.getLogger("job_pipeline")
    level, handlers, propagate = logger.level, logger.handlers[:], logger.propagate
    try:
        yield logger
    finally:
        for handler in logger.handlers[:]:
            if handler not in handlers:
                logger.removeHandler(handler)
                handler.close()
        logger.handlers = handlers
        logger.setLevel(level)
        logger.propagate = propagate


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://h/p?token=abc#x", "https://h/p"),
        ("https://user:password@h:8443/p?token=abc#x", "https://h:8443/p"),
        ("http://[::1]:8080/p?q=secret", "http://[::1]:8080/p"),
        ("https://h", "https://h"),
        ("https://user:secret@[broken/p?q=secret", "<unparseable-url>"),
        ("https://h:bad/p?q=secret", "<unparseable-url>"),
        ("https:///p?q=secret", "<unparseable-url>"),
        ("https://h\\@secret/p", "<unparseable-url>"),
        ("https://h/\nsecret", "<unparseable-url>"),
        ("//user:password@h/p?token=abc", "<unparseable-url>"),
        ("not a URL", "<unparseable-url>"),
    ],
)
def test_redact_url(url: str, expected: str) -> None:
    assert redact_url(url) == expected


def test_redact_message_sanitizes_before_truncating() -> None:
    text = (
        "failed https://user:password@h/p?token=abc#fragment "
        "and HTTPS://h:bad/p?secret=xyz\nBODY-MARKER"
    )
    assert redact_message(text) == "failed https://h/p and <unparseable-url>"
    assert redact_message("  one\t two  ", limit=5) == "one t"
    assert redact_message("\nsecret") == ""
    assert redact_message("hello", limit=0) == ""
    assert "secret" not in redact_message("Authorization: Bearer secret")
    assert "SECRETVALUE" not in redact_message("key sk-test-SECRETVALUE")


@pytest.mark.parametrize(
    "text",
    [
        '{"api_key": "JSON-KEY-VALUE", "token": "JSON-TOKEN-VALUE"}',
        "{'api_key': 'JSON-KEY-VALUE', 'token': 'JSON-TOKEN-VALUE'}",
        r"{\"api_key\": \"JSON-KEY-VALUE\", \"token\": \"JSON-TOKEN-VALUE\"}",
        '{"headers": {"Authorization": "Bearer HEADER-VALUE"}}',
        '{"headers": {"Proxy-Authorization": "Basic HEADER-VALUE"}}',
        '{"access_token": "JSON-TOKEN-VALUE with spaces"}',
        '{"token": "JSON-TOKEN-VALUE escaped \\" trailing value"}',
        'api_key="JSON-KEY-VALUE with spaces"',
    ],
)
def test_quoted_structured_credentials_are_removed(text: str) -> None:
    safe = redact_message(text)
    assert "<redacted>" in safe
    for forbidden in ("JSON-KEY-VALUE", "JSON-TOKEN-VALUE", "HEADER-VALUE", "trailing"):
        assert forbidden not in safe


def test_original_record_is_sanitized_before_root_fanout(
    pipeline_logger: logging.Logger,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = logging.getLogger()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root.addHandler(handler)
    before = (root.level, root.handlers[:], root.filters[:])
    try:
        configure_logging(2)
        assert before == (root.level, root.handlers, root.filters)
        try:
            raise ValueError("EXCEPTION-PAYLOAD BODY-MARKER")
        except ValueError:
            logging.getLogger("job_pipeline.descendant").warning(
                "request %s metadata=%s",
                "https://user:URL-PASSWORD@h/p?token=QUERY-VALUE#FRAGMENT-VALUE",
                '{"token": "JSON-TOKEN-VALUE", "Authorization": "Bearer HEADER-VALUE"}',
                exc_info=True,
                stack_info=True,
            )
        record = next(r for r in caplog.records if r.name == "job_pipeline.descendant")
        assert record.args == ()
        assert record.exc_info is None and record.exc_text is None
        assert record.stack_info is None
        for output in (stream.getvalue(), capsys.readouterr().err, caplog.text):
            assert "https://h/p" in output
            for forbidden in (
                "URL-PASSWORD",
                "QUERY-VALUE",
                "FRAGMENT-VALUE",
                "JSON-TOKEN-VALUE",
                "HEADER-VALUE",
                "EXCEPTION-PAYLOAD",
                "BODY-MARKER",
                "Traceback",
            ):
                assert forbidden not in output
        assert pipeline_logger.propagate is True
    finally:
        root.removeHandler(handler)
        handler.close()


def test_formatting_failure_is_safe_for_root_handlers(
    pipeline_logger: logging.Logger,
    caplog: pytest.LogCaptureFixture,
) -> None:
    configure_logging(2)
    logging.getLogger("job_pipeline.descendant").warning("%d", "BAD-FORMAT-SECRET")
    record = next(r for r in caplog.records if r.name == "job_pipeline.descendant")
    assert record.getMessage() == "<unformattable diagnostic>"
    assert record.args == ()
    assert "BAD-FORMAT-SECRET" not in caplog.text


def test_adversarial_frame_attributes_are_redacted_before_logging(
    pipeline_logger: logging.Logger,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from datetime import datetime
    from typing import cast

    from job_discovery.domain.company import Company
    from job_pipeline.content.model import AcquiredContent, AcquisitionTrace, FrameTrace
    from job_pipeline.runner.worker import WorkerDeps, _log_acquisition

    company = Company(
        name="Acme",
        job_board_url="https://h/jobs",
        sample_job_url="https://h/jobs/1",
    )
    deps = cast(WorkerDeps, type("Deps", (), {"run_id": "run-1"})())
    result = AcquiredContent(
        "BODY-MARKER",
        None,
        "https://h/jobs/1",
        200,
        "rendered_frame_main",
        None,
        None,
        datetime(2026, 1, 1),
        "hash",
        trace=AcquisitionTrace(
            frames_found=1,
            frames=(
                FrameTrace(
                    0,
                    "about",
                    "selected",
                    11,
                    '{"api_key": "ATTR-KEY", "token": "ATTR-TOKEN"}',
                    '{"headers":{"Authorization":"Bearer ATTR-HEADER"}}',
                ),
            ),
            selected_frame=0,
        ),
    )
    configure_logging(2)
    _log_acquisition(deps, 1, company, result.resolved_url, result, force_rendered=True)
    for output in (capsys.readouterr().err, caplog.text):
        assert "acquire frame" in output
        for forbidden in ("ATTR-KEY", "ATTR-TOKEN", "ATTR-HEADER", "BODY-MARKER"):
            assert forbidden not in output


def test_quiet_is_exact_noop(pipeline_logger: logging.Logger) -> None:
    before = (
        pipeline_logger.level,
        pipeline_logger.handlers[:],
        pipeline_logger.propagate,
    )
    configure_logging(0)
    assert before == (
        pipeline_logger.level,
        pipeline_logger.handlers,
        pipeline_logger.propagate,
    )


def test_logging_is_scoped_idempotent_and_resolves_current_stderr(
    pipeline_logger: logging.Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = logging.getLogger()
    httpx = logging.getLogger("httpx")
    before = (root.level, root.handlers[:], httpx.level, httpx.handlers[:])
    configure_logging(1)
    assert pipeline_logger.level == logging.INFO
    configure_logging(2)
    assert pipeline_logger.level == logging.DEBUG
    assert sum(h.name == "job_pipeline.cli" for h in pipeline_logger.handlers) == 1
    assert pipeline_logger.propagate is True
    assert before == (root.level, root.handlers, httpx.level, httpx.handlers)
    for _ in range(2):
        stream = io.StringIO()
        with monkeypatch.context() as patch:
            patch.setattr("sys.stderr", stream)
            logging.getLogger("job_pipeline.test").info("url https://h/p?token=abc#x")
        assert "https://h/p" in stream.getvalue()
        assert "abc" not in stream.getvalue()
        stream.close()


def test_handler_drops_exception_payload(
    pipeline_logger: logging.Logger, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging(1)
    try:
        raise ValueError("BODY-MARKER sk-test-SECRETVALUE")
    except ValueError:
        pipeline_logger.warning("safe failure", exc_info=True)
    error = capsys.readouterr().err
    assert "safe failure" in error
    assert "BODY-MARKER" not in error
    assert "SECRETVALUE" not in error
    assert "Traceback" not in error
