from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from job_discovery.domain.company import Company
from job_discovery.settings import CaptureBrowser
from job_pipeline import settings
from job_pipeline.cli import common, integrate, stages
from job_pipeline.cli.main import build_parser, main
from job_pipeline.content.model import AcquisitionTrace, FrameTrace, StepTrace
from job_pipeline.policy import PipelinePolicy
from job_pipeline.runner import acquisition, capture, worker
from tests.unit.asyncio_harness import run_async
from tests.unit.test_pipeline_diagnostics import pipeline_logger as pipeline_logger

COMPANY = Company(
    name="Acme",
    aliases=("acme",),
    job_board_url="https://acme.example/jobs",
    sample_job_url="https://acme.example/jobs/1",
)
URL = COMPANY.sample_job_url + "?token=source-secret#private"
LEAK = "password=exception-secret https://user:private@acme.example/1?token=hidden"
Result = acquisition.AcquiredContent | acquisition.AcquisitionFailure


def _argv(output: Path, context: str = "http-only") -> list[str]:
    return [
        "integrate",
        "-c",
        "acme",
        "--url",
        URL,
        "--output",
        str(output),
        "--context",
        context,
    ]


def _content() -> acquisition.AcquiredContent:
    text = "PRIVATE POSTING BODY Costa Rica engineer"
    return acquisition.AcquiredContent(
        text=text,
        title="PRIVATE TITLE",
        resolved_url=URL,
        http_status=200,
        method="main_content",
        requisition_id=None,
        jsonld=None,
        retrieved_at=datetime(2026, 11, 1, tzinfo=UTC),
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        acquisition_html="<main>PRIVATE HTML</main>",
        trace=AcquisitionTrace(
            steps=(StepTrace("http", 200, LEAK, 100, False, 0.2),),
            frames_found=1,
            frames=(FrameTrace(0, "same_origin", "selected", 400, LEAK, LEAK),),
            selected_frame=0,
        ),
    )


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, pipeline_logger: Any) -> None:
    monkeypatch.setattr(
        integrate, "find_company", lambda handle: COMPANY if handle == "acme" else None
    )
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    def forbidden(*args: object, **kwargs: object) -> Any:
        pytest.fail(
            "integration must not open a database, call a model, or load dotenv"
        )

    for module, name in (
        (common, "open_database"),
        (common, "database"),
        (common, "require_provider_credential"),
        (stages, "open_database"),
        (stages, "caller_factory"),
        (stages, "LiteLLMCaller"),
        (stages, "preflight_provider"),
        (stages, "run_stage"),
    ):
        monkeypatch.setattr(module, name, forbidden)
    monkeypatch.setattr("dotenv.load_dotenv", forbidden)


@pytest.fixture
def resources(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    events: list[str] = []

    async def validate(url: str) -> str:
        assert url == URL
        events.append("validate")
        return "acme.example"

    @asynccontextmanager
    async def http() -> AsyncIterator[httpx.AsyncClient]:
        events.append("http_open")
        try:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(lambda request: httpx.Response(500))
            ) as client:
                yield client
        finally:
            events.append("http_close")

    @asynccontextmanager
    async def browser(enabled: bool) -> AsyncIterator[CaptureBrowser]:
        assert enabled
        events.append("browser_open")
        try:
            yield cast(CaptureBrowser, object())
        finally:
            events.append("browser_close")

    monkeypatch.setattr(acquisition, "validate_target", validate)
    monkeypatch.setattr(stages, "http_client_factory", http)
    monkeypatch.setattr(stages, "browser_factory", browser)
    return events


def _ladder(
    monkeypatch: pytest.MonkeyPatch, results: list[Result]
) -> list[tuple[bool, bool]]:
    calls: list[tuple[bool, bool]] = []

    async def acquire(url: str, **kwargs: Any) -> Result:
        assert url == URL
        assert kwargs["company"] is COMPANY
        calls.append((kwargs["browser"] is not None, kwargs["force_rendered"]))
        return results[len(calls) - 1]

    monkeypatch.setattr(acquisition, "acquire", acquire)
    return calls


@pytest.mark.parametrize("missing", ["-c", "--url", "--output", "--context"])
def test_required_arguments(tmp_path: Path, missing: str) -> None:
    argv = _argv(tmp_path / "capture")
    index = argv.index(missing)
    del argv[index : index + 2]
    with pytest.raises(SystemExit) as error:
        build_parser().parse_args(argv)
    assert error.value.code == 2


@pytest.mark.parametrize("timeout", ["0", "-1", "no", "1.5", "nan", "inf"])
def test_invalid_timeout(tmp_path: Path, timeout: str) -> None:
    with pytest.raises(SystemExit) as error:
        main([*_argv(tmp_path / "capture"), "--timeout-seconds", timeout])
    assert error.value.code == 2


def test_parser_defaults_and_verbosity(tmp_path: Path) -> None:
    args = build_parser().parse_args([*_argv(tmp_path / "capture"), "-vv"])
    assert args.timeout_seconds == settings.DEFAULT_JOB_TIMEOUT
    assert args.verbose == 2
    assert args.output == tmp_path / "capture"
    assert not hasattr(args, "database")
    assert not hasattr(args, "model")


@pytest.mark.parametrize(
    "context,empty,expected",
    [
        ("http-only", False, [(False, False)]),
        ("http-only", True, [(False, False)]),
        ("browser-fallback", False, [(False, False)]),
        ("browser-fallback", True, [(False, False), (True, True)]),
        ("forced-render", False, [(True, True)]),
    ],
)
def test_contexts_use_shared_ladder_and_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resources: list[str],
    capsys: pytest.CaptureFixture[str],
    context: str,
    empty: bool,
    expected: list[tuple[bool, bool]],
) -> None:
    output = tmp_path / "capture"
    results: list[Result] = [_content()]
    if empty:
        results.insert(0, acquisition.AcquisitionFailure("empty_content", LEAK))
    calls = _ladder(monkeypatch, results)
    if context == "http-only" and empty:
        with pytest.raises(SystemExit) as error:
            main(_argv(output, context))
        assert error.value.code == 1
        assert {path.name for path in output.iterdir()} == {"meta.json"}
    else:
        main(_argv(output, context))
        assert (output / "content.txt").read_text() == _content().text + "\n"
        review = json.loads((output / "review.json").read_text())
        assert review["status"] == "pending"
        assert "pending_review" in capsys.readouterr().out
    assert calls == expected
    assert resources[0] == "validate"
    assert resources[-1] == "http_close"
    assert ("browser_open" in resources) == (context != "http-only")
    if context != "http-only":
        assert resources[-2] == "browser_close"
    metadata = json.loads((output / "meta.json").read_text())
    assert len(metadata["attempts"]) == len(expected)
    assert [attempt["force_rendered"] for attempt in metadata["attempts"]] == [
        forced for _, forced in expected
    ]
    assert metadata["source_url"] == COMPANY.sample_job_url
    for attempt in metadata["attempts"]:
        assert attempt["elapsed_s"] >= 0
    serialized = json.dumps(metadata["attempts"])
    for secret in (
        "exception-secret",
        "private@",
        "hidden",
        "resolved_url",
        "element_id",
        "element_title",
        "PRIVATE",
    ):
        assert secret not in serialized
    assert "source-secret" not in (output / "meta.json").read_text()


@pytest.mark.parametrize("failure", ["unknown", "target", "destination"])
def test_invalid_inputs_open_no_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resources: list[str],
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    output = tmp_path / "capture"
    argv = _argv(output)
    if failure == "unknown":
        argv[2] = LEAK
    elif failure == "destination":
        output.mkdir()
    else:

        async def invalid(url: str) -> str:
            raise ValueError(LEAK)

        monkeypatch.setattr(acquisition, "validate_target", invalid)
    with pytest.raises(SystemExit) as error:
        main(argv)
    assert error.value.code == 2
    assert resources == []
    assert "exception-secret" not in capsys.readouterr().err


@pytest.mark.parametrize("phase", ["validate", "http", "browser", "acquire"])
def test_startup_and_runtime_errors_are_safe_and_close_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resources: list[str],
    capsys: pytest.CaptureFixture[str],
    phase: str,
) -> None:
    async def failed(*args: object, **kwargs: object) -> Any:
        raise RuntimeError(LEAK)

    @asynccontextmanager
    async def failed_context(*args: object) -> AsyncIterator[Any]:
        raise RuntimeError(LEAK)
        yield

    if phase == "validate":
        monkeypatch.setattr(acquisition, "validate_target", failed)
    elif phase == "acquire":
        monkeypatch.setattr(acquisition, "acquire", failed)
    else:
        name = "http_client_factory" if phase == "http" else "browser_factory"
        monkeypatch.setattr(stages, name, failed_context)
    output = tmp_path / "capture"
    with pytest.raises(SystemExit) as error:
        main(_argv(output, "browser-fallback"))
    assert error.value.code == 1
    stderr = capsys.readouterr().err
    assert ("acquisition_failed" if phase == "acquire" else "startup_failed") in stderr
    assert "exception-secret" not in stderr
    assert not output.exists()
    if phase in {"browser", "acquire"}:
        assert resources[-1] == "http_close"
    if phase == "acquire":
        assert resources[-2] == "browser_close"


@pytest.mark.parametrize("publication", [None, "secret_refusal", "publication_io"])
def test_failure_category_survives_publication_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resources: list[str],
    capsys: pytest.CaptureFixture[str],
    publication: str | None,
) -> None:
    _ladder(monkeypatch, [acquisition.AcquisitionFailure("blocked", LEAK, 403)])
    if publication:

        def fail(*args: object, **kwargs: object) -> None:
            raise capture.CapturePublicationError(publication)

        monkeypatch.setattr(capture, "publish_capture", fail)
    with pytest.raises(SystemExit) as error:
        main(_argv(tmp_path / "capture"))
    assert error.value.code == 1
    stderr = capsys.readouterr().err
    assert "acquisition_failed: blocked" in stderr
    if publication:
        assert publication in stderr
    assert "exception-secret" not in stderr


def test_real_secret_refusal_is_atomic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resources: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ladder(monkeypatch, [replace(_content(), frame_html="api_key=private-secret")])
    output = tmp_path / "capture"
    with pytest.raises(SystemExit) as error:
        main(_argv(output))
    assert error.value.code == 1
    assert "secret_refusal" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_preflight_precedes_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resources: list[str],
) -> None:
    real = capture.preflight_output

    def preflight(path: Path) -> None:
        assert resources == []
        real(path)
        resources.append("preflight")

    monkeypatch.setattr(capture, "preflight_output", preflight)
    _ladder(monkeypatch, [_content()])
    main(_argv(tmp_path / "capture"))
    assert resources[:3] == ["preflight", "validate", "http_open"]


def test_per_invocation_attempts_and_verbose_output_safety(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resources: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ladder(monkeypatch, [_content(), _content()])
    for index in range(2):
        output = tmp_path / str(index)
        main([*_argv(output), "-vv"])
        attempts = json.loads((output / "meta.json").read_text())["attempts"]
        assert len(attempts) == 1
        assert attempts[0]["http_status"] == 200
        assert attempts[0]["frames"] == [
            {"index": 0, "origin": "same_origin", "outcome": "selected", "chars": 400}
        ]
    emitted = capsys.readouterr()
    assert "Acquisition status=success" in emitted.err
    for text in ("PRIVATE", "source-secret", "exception-secret", "hidden", "private@"):
        assert text not in emitted.out + emitted.err


def test_timeout_publishes_failure_without_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resources: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def timeout(*args: object, **kwargs: object) -> Any:
        raise TimeoutError(LEAK)

    monkeypatch.setattr(acquisition, "acquire", timeout)
    with pytest.raises(SystemExit) as error:
        main(_argv(tmp_path / "capture", "browser-fallback"))
    assert error.value.code == 1
    assert "acquisition_failed: timeout" in capsys.readouterr().err
    attempts = json.loads((tmp_path / "capture" / "meta.json").read_text())["attempts"]
    assert len(attempts) == 1
    assert attempts[0]["category"] == "timeout"
    assert resources[-2:] == ["browser_close", "http_close"]


def test_cancellation_unwinds_and_main_uses_generic_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resources: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def cancelled(*args: object, **kwargs: object) -> Any:
        raise asyncio.CancelledError

    monkeypatch.setattr(acquisition, "acquire", cancelled)
    with pytest.raises(SystemExit) as error:
        main(_argv(tmp_path / "capture", "forced-render"))
    assert error.value.code == 130
    assert capsys.readouterr().err == "Interrupted.\n"
    assert resources[-2:] == ["browser_close", "http_close"]
    assert list(tmp_path.iterdir()) == []


def test_service_resources_and_timeout_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resources: list[str],
) -> None:
    async def acquire(url: str, **kwargs: Any) -> Result:
        value = kwargs["resources"]
        assert isinstance(value, acquisition.AcquisitionResources)
        assert value.per_host_concurrency == 1
        assert value.http_semaphore._value == 1
        assert value.browser_semaphore._value == 1
        assert value.per_host_semaphores == {}
        assert kwargs["timeout_seconds"] == 7
        assert kwargs["force_rendered"] is True
        assert callable(kwargs["on_attempt"])
        return _content()

    monkeypatch.setattr(acquisition, "acquire_posting", acquire)
    main([*_argv(tmp_path / "capture", "forced-render"), "--timeout-seconds", "7"])


def test_cancelled_task_closes_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resources: list[str],
) -> None:
    async def scenario() -> None:
        entered = asyncio.Event()

        async def blocking(*args: object, **kwargs: object) -> Any:
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(acquisition, "acquire", blocking)
        args = build_parser().parse_args(_argv(tmp_path / "capture", "forced-render"))
        task = asyncio.create_task(integrate._integrate(args, COMPANY))
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run_async(scenario())
    assert resources[-2:] == ["browser_close", "http_close"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "context,category",
    [
        ("http-only", "empty_content"),
        ("browser-fallback", "empty_content"),
        ("browser-fallback", "blocked"),
        ("forced-render", "empty_content"),
    ],
)
def test_worker_adapter_and_cli_use_equivalent_ladder_sequences(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resources: list[str],
    context: str,
    category: str,
) -> None:
    failure = acquisition.AcquisitionFailure("empty_content", LEAK)
    if category == "blocked":
        failure = acquisition.AcquisitionFailure("blocked", LEAK)
    calls = _ladder(monkeypatch, [failure, _content(), failure, _content()])
    output = tmp_path / "capture"
    try:
        main(_argv(output, context))
    except SystemExit as exc:
        assert exc.code == 1
    cli_calls = list(calls)
    calls.clear()

    async def scenario() -> Result:
        async with stages.http_client_factory() as http:
            deps = SimpleNamespace(
                http_client=http,
                browser=None if context == "http-only" else object(),
                http_semaphore=asyncio.Semaphore(1),
                browser_semaphore=asyncio.Semaphore(1),
                per_host_semaphores={},
                per_host_lock=asyncio.Lock(),
                policy=PipelinePolicy(),
                run_id="offline-test",
            )
            return await worker._acquire_content(
                cast(worker.WorkerDeps, deps),
                COMPANY,
                URL,
                1,
                force_rendered=context == "forced-render",
            )

    monkeypatch.setattr(worker, "_log_acquisition", lambda *args, **kwargs: None)
    result = run_async(scenario())
    assert calls == cli_calls
    metadata = json.loads((output / "meta.json").read_text())
    assert metadata["status"] == (
        "failure" if isinstance(result, acquisition.AcquisitionFailure) else "success"
    )


@pytest.mark.parametrize("category", ["publication_io", "destination_conflict"])
def test_output_preflight_failure_prevents_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resources: list[str],
    category: str,
) -> None:
    def fail(output: Path) -> None:
        raise capture.CapturePublicationError(category)

    monkeypatch.setattr(capture, "preflight_output", fail)
    with pytest.raises(SystemExit) as error:
        main(_argv(tmp_path / "capture"))
    assert error.value.code == (2 if category == "destination_conflict" else 1)
    assert resources == []


def test_unexpected_publication_error_is_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resources: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ladder(monkeypatch, [_content()])

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError(LEAK)

    monkeypatch.setattr(capture, "publish_capture", fail)
    with pytest.raises(SystemExit) as error:
        main(_argv(tmp_path / "capture"))
    assert error.value.code == 1
    assert capsys.readouterr().err == "publication_error: capture publication failed\n"
    assert resources[-1] == "http_close"
