# Phase 1 — Foundations

Goal: the three packages exist, build, ship in the wheel, pass the layering
test, and the pipeline's tables are created by Alembic and verified by the
parity test. No behaviour yet. One pull request.

Strategy: incremental. Every task leaves the suite green, and the first
commit after this phase can already run `job-pipeline --help` from an
installed wheel.

## Task 1.1 — Package skeletons and build configuration

- Objective: register the packages with the build, the type checker, and
  the linter so every later task is checked from its first line.
- Files: `pyproject.toml`, `src/job_eligibility/__init__.py`,
  `src/job_extractor/__init__.py`, `src/job_pipeline/__init__.py`,
  `src/job_pipeline/cli/__init__.py`, `src/job_pipeline/cli/main.py`.
- Changes:
  - [ ] `[tool.hatch.build.targets.wheel] packages` lists all four `src/`
        packages. Keep the existing `include` for the JS assets.
  - [ ] `[project.scripts]` adds `job-pipeline = "job_pipeline.cli.main:main"`.
  - [ ] `[tool.ruff.lint.isort] known-first-party` lists all four packages.
  - [ ] `dependencies` adds `trafilatura` (pin to the current release at
        implementation time). `litellm`, `httpx`, `pydantic`, `playwright`,
        `aiosqlite`, and `sqlalchemy[asyncio]` are already present.
  - [ ] `src/job_pipeline/cli/main.py` is a copy of the discovery dispatcher
        shape: a frozen `Subcommand` dataclass and a `SUBCOMMANDS` tuple,
        initially holding a single `preview` placeholder that prints
        "not implemented" and exits 2. Phase 5 fills the table.
  - [ ] Each package `__init__.py` carries a module docstring stating its
        responsibility and its dependency rule in one paragraph.
  - [ ] `uv lock` so `uv lock --check` in CI passes.
- Dependencies: none.
- Verification: `uv sync --extra dev --group test`, `uv build`, install the
  wheel into a scratch venv, `job-pipeline --help` exits 0,
  `python -c "import job_eligibility, job_extractor, job_pipeline"`.
- Effort: 2 hours.

## Task 1.2 — Generalize the layering test to several package roots

- Objective: the import-graph test covers the new packages from the first
  commit, so a leaf importing the database fails in CI rather than in review.
- Files: `tests/unit/test_layering.py`.
- Changes:
  - [ ] Replace `PACKAGE_NAME` / `SRC_ROOT` with a table of roots:
        `{"job_discovery": SRC/"job_discovery", ...}`.
  - [ ] Keep the per-unit allowlist for `job_discovery` byte-identical.
  - [ ] Add a per-unit allowlist for `job_pipeline` with units `cli`,
        `runner`, `content`, `identity`, `persistence`, `llm`, `policy`,
        `settings`, root. Units that do not exist yet are added in the task
        that creates them; the coverage test fails until they are listed,
        which is the intended forcing function.
  - [ ] Add a cross-package table `ALLOWED_PACKAGE_IMPORTS`:
        `job_discovery -> {}`, `job_eligibility -> {}`, `job_extractor -> {}`,
        `job_pipeline -> {job_discovery, job_eligibility, job_extractor}`.
        Within `job_pipeline -> job_discovery`, restrict to the units
        `domain`, `catalog`, `persistence`, `settings` by name.
  - [ ] Assert by name, as `FORBIDDEN_FOR_EXTRACTION` does today, that the
        two leaves import nothing from any repo package, and that
        `job_discovery` imports nothing from the three new packages.
  - [ ] The relative-import check runs over every root.
- Dependencies: 1.1.
- Verification: `uv run --group test pytest tests/unit/test_layering.py`;
  temporarily add `from job_pipeline import x` to a leaf and confirm the test
  names the edge.
- Effort: 3 hours.

## Task 1.3 — Pre-commit, CI, and mypy coverage of the new trees

- Objective: the gates that protect `job_discovery` protect the new code on
  the same terms.
- Files: `.pre-commit-config.yaml`, `.github/workflows/ci.yml`,
  `pyproject.toml` (`[tool.mypy] files` already covers `src`, confirm).
- Changes:
  - [ ] Pre-commit `snapshot-regression` hook `files` regex: replace
        `src/job_discovery/.*` with `src/.*`.
  - [ ] CI `package` job: run `job-pipeline --help` alongside the two
        existing console scripts; extend the "every module the package
        imports is installed" step to walk all four packages.
  - [ ] CI `package` job: after the migrate up/down/up step, add
        `job-pipeline preview --all --database "$RUNNER_TEMP/smoke.db"`
        once Phase 5 lands. Leave a commented line now so the step is not
        forgotten.
  - [ ] uv version pin in the workflow untouched.
- Dependencies: 1.1.
- Verification: `uv run --extra dev pre-commit run --all-files`; a draft PR
  shows `ci-ok` green with the new steps visible in the package job log.
- Effort: 1.5 hours.

## Task 1.4 — Pipeline settings

- Objective: one module of constants for the pipeline, mirroring
  `job_discovery.settings`, so no default is buried in a function.
- Files: `src/job_pipeline/settings.py`.
- Changes:
  - [ ] `DEFAULT_MODEL = "gpt-4.1-mini"`, `DEFAULT_HTTP_CONCURRENCY = 10`,
        `DEFAULT_BROWSER_CONCURRENCY = 2`, `DEFAULT_MODEL_CONCURRENCY = 4`,
        `DEFAULT_PER_HOST_CONCURRENCY = 2`, `DEFAULT_JOB_TIMEOUT = 180 s`,
        `RETRY_LIMIT = 3`, `RETRY_BACKOFF = timedelta(days=1)`,
        `FETCH_TIMEOUT = 30 s`, `MIN_CONTENT_CHARS = 400`,
        `FINGERPRINT_CHARS = 2000`.
  - [ ] Re-export `DATABASE_PATH` and `DATABASE_ENV_VAR` from
        `job_discovery.settings` rather than redefining them: one database,
        one source of truth for its path.
  - [ ] Docstring explains each number in one sentence, as discovery's
        settings do.
- Dependencies: 1.1.
- Verification: `tests/unit/test_pipeline_settings.py` asserts the retry
  limit is positive, the backoff is positive, and the database path is the
  identical object discovery exports.
- Effort: 1 hour.

## Task 1.5 — Models

- Objective: the seven tables from the overview as SQLAlchemy 2.x models on
  the shared `Base`.
- Files: `src/job_pipeline/persistence/__init__.py`,
  `src/job_pipeline/persistence/models.py`.
- Changes:
  - [ ] `from job_discovery.persistence.models import Base` — the same
        metadata, so `ForeignKey("companies.slug")` resolves and
        `missing_tables` and the parity test see the new tables once this
        module is imported.
  - [ ] Status, phase, outcome, decision, trigger, and error-category
        literals defined once as tuples and reused by `CheckConstraint`s,
        exactly as `RUN_STATUSES` is.
  - [ ] `jobs`: unique `(company_slug, url_key)`; index on `(status,
        next_attempt_after)` for the planner; `merged_into_id` self FK
        nullable; `company_slug` FK with `ondelete="RESTRICT"`.
  - [ ] `job_sources`: unique `(job_id, url)`; FK to `jobs` cascade (a
        source belongs to its job).
  - [ ] `job_contents`: unique `(job_id, content_hash)`; index on
        `(fingerprint)` and `(requisition_id)` for the merge lookups;
        `text` as `Text`, `jsonld_json` as `Text` nullable.
  - [ ] `job_attempts`: FK to `jobs` and to `pipeline_runs`; index on
        `(job_id, started_at)`; token columns nullable integers.
  - [ ] `job_decisions` and `job_results`: FK to `jobs`, `job_attempts`,
        `job_contents`; partial uniqueness "one current row per job" is
        enforced in the repository (SQLite partial indexes are supported
        but Alembic autogenerate does not render them; note this in the
        docstring).
  - [ ] `pipeline_runs`: string primary key minted by the scheduler.
  - [ ] Module docstring carries the RESTRICT rationale and the single-Base
        rationale from the overview.
- Dependencies: 1.4.
- Verification: `uv run mypy`; importing the module registers seven new
  tables in `Base.metadata.tables`.
- Effort: 3 hours.

## Task 1.6 — Migration and parity

- Objective: the tables are created by Alembic, on one head, and the
  parity test proves models and migration agree.
- Files: `migrations/env.py`, `migrations/versions/<rev>_job_pipeline_tables.py`,
  `tests/unit/test_persistence.py`.
- Changes:
  - [ ] `migrations/env.py` imports `job_pipeline.persistence.models` for
        its registration side effect, with a comment saying why the import
        looks unused.
  - [ ] `uv run alembic revision --autogenerate -m "job pipeline tables"`,
        then review: every CHECK constraint present, every index present,
        `down_revision = "adc90cfcd42b"`.
  - [ ] `TestMigrationParity` imports `job_pipeline.persistence.models` at
        module level so `compare_metadata` sees the full schema; otherwise
        the migrated database would carry tables the metadata lacks and the
        test would fail for the wrong reason.
  - [ ] `_fresh_database` in the same file is unchanged; the new
        repository tests in Task 1.7 build from `Base.metadata` the same
        way.
- Dependencies: 1.5.
- Verification: `uv run alembic upgrade head` on a temp path, `alembic
  downgrade -1`, `upgrade head` again; `uv run alembic heads` prints one
  head; the parity test passes.
- Effort: 2 hours.

## Task 1.7 — Repositories

- Objective: every SQL statement the pipeline will need, each in one home,
  each owning its own transaction, tested against a real SQLite file.
- Files: `src/job_pipeline/persistence/jobs_repo.py`,
  `contents_repo.py`, `attempts_repo.py`, `decisions_repo.py`,
  `results_repo.py`, `runs_repo.py`, `tests/unit/test_pipeline_persistence.py`.
- Changes:
  - [ ] `jobs_repo`: `observe(session, company_slug, observations:
        Sequence[(url_key, raw_url)], seen_at) -> ObserveSummary` upserts
        jobs and sources in one transaction and returns counts of new and
        re-seen; `claim(session, job_id) -> bool` is the conditional update;
        `release(session, job_id, *, status, next_phase, next_attempt_after)`;
        `mark_merged(session, job_id, into_id)`; `list_work(session,
        snapshot_keys, now, limit)`; `get(session, job_id)`;
        `find_by_requisition(session, company_slug, requisition_id)`;
        `find_by_fingerprint(session, company_slug, fingerprint)`;
        `disposition_counts(session, snapshot_keys)` for preview.
  - [ ] `contents_repo`: `store(session, job_id, acquired) -> content_id`
        idempotent on `(job_id, content_hash)`; `latest(session, job_id)`.
  - [ ] `attempts_repo`: `start`, `finish`, with `fail` as a `finish`
        variant; `history(session, job_id)`.
  - [ ] `decisions_repo`: `accept(session, ...)` flips `is_current` on
        prior rows inside the same transaction; `current(session, job_id)`.
  - [ ] `results_repo`: same shape as decisions.
  - [ ] `runs_repo`: `start_run`, `finish_run(counts)`, `reap_stale(older_than)`
        releasing `in_progress` jobs back to `retry` with
        `error_category = "interrupted"` recorded on their open attempt.
  - [ ] All timestamps pass through `as_naive_utc`. No repository imports
        another repository.
  - [ ] Tests follow `test_persistence.py`: real file under `tmp_path`,
        schema from `Base.metadata`, driven by `run_async`/`track_engine`.
        Cover: observe twice is idempotent and bumps `last_seen_at`; claim
        wins once under two concurrent claimants; accept supersedes the
        prior current row; reap releases only rows older than the bound;
        RESTRICT blocks deleting a company with jobs.
- Dependencies: 1.6.
- Verification: `uv run --group test pytest tests/unit/test_pipeline_persistence.py`.
- Effort: 6 hours.

## Task 1.8 — Preflight reuse

- Objective: the pipeline CLI refuses to run against an unmigrated
  database with the same message discovery gives.
- Files: `src/job_pipeline/cli/main.py` (temporary), later `cli/common.py`.
- Changes:
  - [ ] Import `job_pipeline.persistence.models` before calling
        `job_discovery.persistence.engine.missing_tables`, so the shared
        metadata includes the new tables. Document the ordering in a
        comment; it is the one place the single-Base decision is visible.
  - [ ] Reuse `database()` and the `_migrate_hint` text pattern from
        `job_discovery.cli.scan` by copying the three-line function, not by
        importing a private name.
- Dependencies: 1.7.
- Verification: against an empty temp database, `job-pipeline preview --all`
  exits 2 naming the seven missing tables; after `alembic upgrade head` it
  reaches the placeholder.
- Effort: 1 hour.

## Testing strategy

| Test type | Scope | Priority |
|---|---|---|
| Unit | layering over four roots; settings; repositories on a real file | P0 |
| Unit | migration parity with both model modules imported | P0 |
| Integration | wheel install plus `job-pipeline --help` in the CI package job | P0 |
| Manual | `alembic upgrade head` on a copy of `data/job-discovery.db` | P1 |

## Done

- [ ] One PR, title `feat: scaffold job_pipeline, job_eligibility, job_extractor packages and tables`.
- [ ] `ci-ok` green; CI still holds no secrets.
- [ ] `uv run alembic heads` shows one head.
- [ ] `CLAUDE.md` layering table gains the four-root form (final wording in Phase 6, a one-paragraph stub now).
