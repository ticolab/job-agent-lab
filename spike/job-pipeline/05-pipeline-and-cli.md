# Phase 5 — Lifecycle, runner, and CLI

Goal: the daily run. Observe the snapshot, plan the work, process each job
through fetch, eligibility, and extraction with isolation and retries, and
expose preview, run, reprocess, and show. Two pull requests: runner, then
CLI.

The shape is `job_discovery.batch` applied per job: a policy module that
decides, a worker that owns one job's lifecycle, a scheduler that is a set
of semaphores and a gather. No framework.

## Task 5.1 — Policy

- Objective: every decision that is not "process this job", in one module.
- Files: `src/job_pipeline/policy.py`, `tests/unit/test_pipeline_policy.py`.
- Changes:
  - [ ] `PipelinePolicy(frozen)`: `http_concurrency`, `browser_concurrency`,
        `model_concurrency`, `per_host_concurrency`, `job_timeout`,
        `retry_limit`, `retry_backoff`, `limit: int | None`, `model: str`.
        Defaults from `job_pipeline.settings`.
  - [ ] `classify_failure(exc_or_failure) -> (category, retryable: bool)`
        mapping `AcquisitionFailure` categories, `ModelCallError`,
        `ModelResponseError`, `UngroundedEvidenceError`, `TimeoutError`, and
        unexpected exceptions (`internal`, retryable once).
  - [ ] `next_state(job, category, retryable, policy) -> (status,
        next_attempt_after)`: retryable and `attempt_count < retry_limit`
        gives `retry` with `now + backoff * attempt_count`; otherwise
        `failed`, except terminal acquisition categories which give
        `unavailable`.
  - [ ] `work_filter`: `new`, or `retry` with `next_attempt_after <= now`,
        and `url_key` in the snapshot. Reprocess ignores both.
- Dependencies: Phases 1 and 2.
- Verification: table-driven tests over every category and attempt count.
- Effort: 3 hours.

## Task 5.2 — Observation

- Objective: fold today's `job_urls` into `jobs` without network or model.
- Files: `src/job_pipeline/runner/observe.py`,
  `tests/unit/test_pipeline_observe.py`.
- Changes:
  - [ ] `observe(session_factory, companies) -> ObservationSummary`: read
        `job_urls` for the selected companies through
        `job_discovery.persistence.jobs_repo.list_company_urls`, compute
        `url_key` per URL, call `jobs_repo.observe` per company in its own
        session, return per-company new and re-seen counts plus the
        snapshot key set for the planner.
  - [ ] Two raw URLs in one snapshot with one key are two `job_sources`
        rows under one job; the summary counts the collapse so the operator
        sees the identity rule at work.
  - [ ] Tests on a real file: first observation creates, second bumps
        `last_seen_at` only, a URL that leaves the snapshot leaves its job
        untouched.
- Dependencies: 5.1.
- Verification: unit tests.
- Effort: 3 hours.

## Task 5.3 — Worker

- Objective: one job end to end, never raising for a job-level failure,
  never holding a session across an await on the network or the model.
- Files: `src/job_pipeline/runner/worker.py`,
  `tests/unit/test_pipeline_worker.py`.
- Changes:
  - [ ] `JobOutcome(frozen)`: `job_id`, `company_slug`, `status`,
        `phase_reached`, `decision | None`, `error | None`, `tokens`.
  - [ ] `process_job(job_id, *, deps: WorkerDeps) -> JobOutcome` where
        `WorkerDeps(session_factory, http_client, browser | None, caller:
        StructuredCaller, policy, run_id, trigger, reason)`. Everything is
        injected; the worker imports no engine, no litellm, no catalog.
  - [ ] Steps, each in its own short session where it touches the database:
        1. `claim`; a lost claim returns a `skipped` outcome.
        2. `attempts_repo.start`.
        3. If `next_phase == fetch`: `acquire`; on failure record and
           release per policy. On success `contents_repo.store`; run the
           merge lookups (requisition id, then fingerprint) against other
           jobs of the same company; a hit marks `merged` and stops.
        4. If `next_phase <= eligibility`: build messages, `caller.complete`
           under the model semaphore, `validate`, `decisions_repo.accept`.
           `ineligible` or `unknown` release to the matching terminal
           status and stop. `eligible` sets `next_phase = extraction`.
        5. Extraction: same pattern with the extractor leaf and
           `results_repo.accept`; success releases to `completed_eligible`.
        6. `attempts_repo.finish` with phase, outcome, tokens.
  - [ ] `asyncio.wait_for(..., policy.job_timeout)` around steps 3 to 5.
  - [ ] The broad `except Exception` boundary mirrors
        `job_discovery.batch.worker`; `BaseException` subclasses propagate.
        A failure to record a failure is logged and left for the reaper.
  - [ ] One INFO log line per job with slug, key, phase, outcome.
  - [ ] Tests with respx, a fake browser, and `ReplayCaller`: happy path to
        `completed_eligible`; ineligible stops before extraction; eligible
        then extraction validation failure leaves `retry` with
        `next_phase = extraction` and reuses the stored content and decision
        on the retry (assert the caller was invoked once for eligibility
        across both attempts); 404 gives `unavailable`; a timeout gives
        `retry` then `failed` after the limit; a fingerprint match gives
        `merged`; a lost claim gives `skipped`.
- Dependencies: 5.1, Phases 2 to 4.
- Verification: unit tests; `uv run mypy`.
- Effort: 8 hours.

## Task 5.4 — Scheduler

- Objective: reap, observe, plan, fan out, aggregate.
- Files: `src/job_pipeline/runner/scheduler.py`,
  `tests/unit/test_pipeline_scheduler.py`.
- Changes:
  - [ ] `new_run_id()` with the discovery batch-id format.
  - [ ] `run_pipeline(companies, *, session_factory, deps_factory, policy,
        reprocess: Sequence[int] | None = None, reason: str | None = None)
        -> PipelineResult`:
        1. `runs_repo.reap_stale(now - job_timeout)`.
        2. `runs_repo.start_run`.
        3. `observe` unless reprocessing.
        4. `jobs_repo.list_work(snapshot_keys, now, limit)` or the
           reprocess ids.
        5. Semaphores: http, browser, model, and a per-host dict created
           lazily; the worker receives them through `WorkerDeps`.
        6. `asyncio.gather` over `process_job`, no `return_exceptions`.
        7. `runs_repo.finish_run(counts)`.
  - [ ] `PipelineResult(frozen)`: `run_id`, `observation`, `outcomes`,
        `reaped`, with counted properties for every disposition in
        REQ-OPS-6: skipped completed, attempted, eligible, ineligible,
        unknown, unavailable, retry, failed, merged, interrupted.
  - [ ] Browser is launched once per run only if any selected company is a
        DOM board or any job's last failure was `empty_content`; closed in
        `finally`.
  - [ ] Tests with a fake worker: ceilings observed via high-water marks
        gated on an explicit target, the `test_batch.py` technique;
        `limit` honoured; reprocess bypasses the snapshot filter.
- Dependencies: 5.3.
- Verification: unit tests.
- Effort: 5 hours.

## Task 5.5 — CLI: shared pieces

- Objective: selection, database, and preflight shared by every subcommand.
- Files: `src/job_pipeline/cli/common.py`, `tests/unit/test_pipeline_cli_common.py`.
- Changes:
  - [ ] `add_selection_arguments(parser)`: `--all`, `-c/--company`,
        `--companies FILE`, `--exclude`, resolved through
        `job_discovery.catalog.find_company` with the same error texts as
        `scan`. Copied, not imported from `job_discovery.cli`, which is not
        in the allowed set.
  - [ ] `add_database_argument`, `open_database(args)` with the preflight
        from Task 1.8 and the migrate hint.
  - [ ] `--model`, `--limit`, the three concurrency flags, `--timeout-seconds`.
  - [ ] Exit code contract: 0 completed even with job failures; 2 cannot
        run.
- Dependencies: Phase 1.
- Verification: parser tests built from `build_parser`, never hand-made
  namespaces, as `test_cli_scan.py` does.
- Effort: 3 hours.

## Task 5.6 — `preview`

- Objective: dispositions without variable cost.
- Files: `src/job_pipeline/cli/preview.py`, `tests/unit/test_pipeline_cli_preview.py`.
- Changes:
  - [ ] Runs `observe` for the selection (writes `jobs` and `job_sources`
        only, which is bookkeeping, not cost; a `--no-observe` flag reads
        without writing), then `disposition_counts` against the snapshot
        and prints a table: new, retry due, retry waiting, in progress,
        completed eligible, completed ineligible, unknown, unavailable,
        failed, merged; and "would process N" with the `--limit` applied.
  - [ ] Per-company breakdown with `--by-company`.
- Dependencies: 5.2, 5.5.
- Verification: CLI test against a temp database seeded through the
  discovery repositories; asserts no network client is constructed.
- Effort: 3 hours.

## Task 5.7 — `run`

- Objective: the daily command.
- Files: `src/job_pipeline/cli/run.py`, `tests/unit/test_pipeline_cli_run.py`.
- Changes:
  - [ ] Loads `.env`, builds `PipelinePolicy`, opens the database, builds
        `LiteLLMCaller`, an `httpx.AsyncClient`, and the deps factory, calls
        `run_pipeline`, prints the summary: run id, observation counts,
        every disposition, tokens, elapsed, and a failures list with slug,
        key, phase, category, message.
  - [ ] `--dry-run` is `preview`.
  - [ ] `--json-output DIR` writes one JSON per accepted result through a
        single renderer in `job_pipeline/reporting.py`.
  - [ ] Test drives the real command against a temp database with respx,
        a fake browser factory, and `ReplayCaller` injected through a
        module-level factory the test monkeypatches, the `test_cli_scan.py`
        pattern.
- Dependencies: 5.4, 5.5.
- Verification: CLI test; `uv run job-pipeline run -c <one api board> --limit 3`
  against a copy of the production database.
- Effort: 4 hours.

## Task 5.8 — `reprocess` and `show`

- Objective: the explicit, auditable escape hatch and the audit view.
- Files: `src/job_pipeline/cli/reprocess.py`, `src/job_pipeline/cli/show.py`,
  tests for each.
- Changes:
  - [ ] `reprocess --job ID [--job ID ...] --reason TEXT
        [--from-phase fetch|eligibility|extraction]` (default `fetch`);
        also `--status unknown -c HANDLE --reason TEXT` to re-run every
        unknown job of a company after a policy change. Sets `next_phase`,
        records `trigger = "reprocess"` and the reason on the attempt, and
        runs the scheduler for exactly those ids. Prior decisions and
        results keep their rows with `is_current = false`.
  - [ ] `show --job ID` or `show --url URL` prints the job row, every
        source URL, every content version with hash and method, every
        attempt with phase, outcome, category, tokens, every decision with
        evidence, and the current result as indented JSON.
- Dependencies: 5.4, 5.5.
- Verification: CLI tests; `reprocess` on a completed job creates a second
  attempt and a second decision row and leaves the first intact.
- Effort: 4 hours.

## Task 5.9 — Dispatcher and CI smoke

- Objective: the four subcommands registered, and the package job exercises
  the real command.
- Files: `src/job_pipeline/cli/main.py`, `.github/workflows/ci.yml`.
- Changes:
  - [ ] `SUBCOMMANDS` rows for preview, run, reprocess, show.
  - [ ] Uncomment the `job-pipeline preview --all --database "$RUNNER_TEMP/smoke.db"`
        step from Task 1.3; it runs after the migrate step against a
        database with no `job_urls`, so it must print zero dispositions and
        exit 0.
- Dependencies: 5.6 to 5.8.
- Verification: CI package job green on both OSes.
- Effort: 1 hour.

## Testing strategy

| Test type | Scope | Priority |
|---|---|---|
| Unit | policy tables; observe idempotency; worker paths; scheduler ceilings | P0 |
| Unit | CLI commands against a temp database with fakes | P0 |
| Integration | CI package job runs `preview` on a fresh migrated database | P0 |
| Manual | `run -c <api board> --limit 3` on a database copy; `show` on one result | P0 |

## Done

- [ ] PR 1 `feat: add the job pipeline runner with per-job isolation and retry policy`.
- [ ] PR 2 `feat: add the job-pipeline CLI with preview, run, reprocess, and show`.
- [ ] A run against a database copy ends with every attempted job in a terminal or retry status.
- [ ] A second run on the same copy makes zero model calls, shown by the attempt count not changing.
