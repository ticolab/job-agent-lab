# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`vacantes` answers one question a few times a week: **what job postings are open right now.** It extracts job-posting URLs from a corpus of real career sites, built around one decision — a `browser-use` agent (OpenAI model via LiteLLM) handles the non-deterministic UI work on a career page (filtering by region, revealing dynamic content) while the actual link extraction is **deterministic**: a JS matcher runs in the page and collects same-origin `<a>` tags whose path matches a prefix derived from each company's `sample_job_url`. Never replace the deterministic matcher with LLM-guessed link lists.

Only one of the seven registered strategies drives an agent at all. The other six are plain HTTP against the ATS or search platform backing the board, so "agent" names a mechanism inside `extraction/dom/`, not the system.

Two purposes share one core. The `integrate` CLI onboards and debugs one board at a time and writes a JSON artifact a human reviews before a catalog entry is committed; the `batch` scheduler runs the corpus concurrently and persists the current URL set. Both call the same `extract` coroutine with the same `RunContext`, which is what keeps the integration workflow meaningful as a correctness signal for scheduled runs.

Detailed per-feature design notes (SYS-N history, matcher semantics, adapter wire contracts) live in `TABNINE.md`; architecture rationale in `ARCHITECTURE.md`; known site blocker classes (C1–C22) in `blockers/`. `TRANSITION.md` holds the restructuring plan; landed phases stay in it, marked as such with their as-built deviations, and the whole file is removed when the last phase lands.

## Commands

```bash
uv sync --extra dev                  # runtime + dev deps
uv sync --group test                 # pytest, pytest-playwright, respx
uv run playwright install chromium   # needed by live agent AND snapshot tests

uv run job-agent-lab                 # run all companies in the catalog
uv run job-agent-lab -c akurey       # one company (alias > acronym > substring match)
uv run job-agent-lab -c gap --headed --model gpt-4o --max-steps 25

uv run vacantes --help               # dispatcher: integrate | batch
uv run vacantes integrate -c akurey  # identical to the job-agent-lab form above

uv run ruff format . && uv run ruff check . --fix
uv run mypy src scripts tests
uv run --group test pytest tests/                # full suite
uv run --group test pytest tests/unit/           # browser-free (schema, adapters, prompt)
uv run --group test pytest tests/snapshots/      # needs Chromium (frozen-DOM regression)
uv run --group test pytest tests/unit/test_coveo.py -k pagination   # single test
```

Two console scripts are registered. `vacantes` is the dispatcher with `integrate` and `batch` subcommands; `job-agent-lab` is a frozen alias pointing straight at the integrate entry point rather than through the dispatcher, so its argument surface is the pre-rename CLI's by construction. Both populate their parser from `add_integrate_arguments`, so the two surfaces cannot drift. Keep `job-agent-lab` working — every doc, skill, and habit is built on it.

`OPENAI_API_KEY` in `.env` is required only for live `strategy="dom"` agent runs. Unit tests, snapshot tests, API strategies, and `pre_filter_urls` companies all run without it.

Pre-commit gates: ruff, mypy, and the full test suite runs on any change under `src/vacantes/{extraction,domain}/` or `tests/`. The mypy hook type-checks only the *staged* files and is stricter than the repo-wide run, so a green `uv run mypy src scripts tests` is not a guarantee the commit passes.

Alembic owns the schema. The database lives at `data/vacantes.db`, gitignored because the dataset answers "what is open right now" and is regenerable by re-running a batch.

```bash
uv run alembic upgrade head                        # create or migrate the database
uv run alembic revision --autogenerate -m "..."    # new revision from models.py
sqlite3 data/vacantes.db 'select * from job_urls'  # query it directly
```

`migrations/env.py` builds the connection URL from `settings.DATABASE_PATH`; `sqlalchemy.url` in `alembic.ini` is deliberately empty so the path has one source of truth. That path defaults to `data/vacantes.db` and is overridden by the `VACANTES_DB` **shell** variable (not a `.env` key, because Alembic never loads `.env`). `vacantes batch --database PATH` points one run elsewhere, but Alembic has no such flag, so a non-default database is migrated with `VACANTES_DB=PATH uv run alembic upgrade head`; the schema preflight prints exactly that command when it applies.

`vacantes batch` is the only writer, and it needs those migrations already applied — it preflights the schema and names the missing tables rather than dying inside a repository. A selection is mandatory: there is no bare invocation meaning "the whole corpus", because most of the corpus costs a Chromium context and a paid model.

```bash
uv run vacantes batch --dry-run --all                  # plan only, writes nothing
uv run vacantes batch -c speechify -c cloudbeds        # two boards
uv run vacantes batch --all --exclude accenture        # everything but one
uv run vacantes batch --companies queue.txt --force    # a file of handles, ignore freshness
```

The full surface is `(--all | -c HANDLE ... | --companies FILE)` plus `--exclude`, `--browser-concurrency`, `--http-concurrency`, `--freshness-hours`, `--force`, `--timeout-seconds`, `--dry-run`, `--json-output`, `--database`, `-m/--model`, and `--max-steps`. Handles resolve exactly as `integrate -c` resolves them, and an unknown one aborts before any work starts.

Four things there are decisions rather than accidents. The mandatory selection is one, for the cost reason above. A completed batch **exits zero even when individual boards failed**, matching `integrate`'s default — so query `company_runs` for board health instead of reading the exit code, and read exit 2 as "the batch could not run at all" (unknown handle, empty selection, unmigrated database). The aggregate summary is rendered by `cli/batch.py` while per-company JSON still goes through `reporting.output.save_result`, so there is still exactly one JSON renderer. And there is deliberately no `--headed`, because several concurrent headed browsers is not a debugging mode; debug a board through `integrate`.

Reach for `--dry-run` first when a batch misbehaves: it prints what would run, what is skipped as fresh, and the ceilings, without writing even the catalog projection. Note that `--timeout-seconds` doubles as the reaper's staleness bound, so lowering it to reap sooner also shortens every run's ceiling.

Commit directly to `main` — no feature branches in this repo. Never bypass the pre-commit hooks with `--no-verify`; if an auto-fixer modifies a file, re-stage and commit again.

Commit messages are a **single short sentence, 100 characters max** — subject line only, no body. Reasoning belongs in the code, the design docs, or `blockers/`, not in the commit message.

## Architecture

### Components and the shared kernel

Three components sit over one shared kernel. `extraction/` owns the port and every strategy. `persistence/` owns the database. `batch/` owns concurrency and the per-company unit of work. The kernel — `domain/`, `catalog/`, `settings.py` — is the vocabulary all three speak.

All three exist today, alongside `cli/` and `reporting/`, and both subcommands are wired: `vacantes integrate` drives one board at a time, `vacantes batch` drives the corpus and is the only thing in the system that writes to the database.

Dependencies point inward and never cycle:

```
Enforced today by tests/unit/test_layering.py:

  cli         → batch, catalog, domain, extraction, persistence, reporting,
                settings
  batch       → domain, extraction, persistence
  extraction  → domain, settings
  persistence → domain, settings
  reporting   → catalog
  catalog     → domain
  settings    → (nothing)
  domain      → (nothing inside vacantes)
```

Two rows are narrower than `TRANSITION.md` §2.3 anticipated, and both for the same reason: a component that is *handed* what it needs does not import it. `persistence` does not reach `catalog` because `sync_catalog` receives the companies to write as an argument rather than importing `COMPANIES`. `batch` reaches neither `catalog` nor `settings` — the scheduler is given both the companies to run and the session factory to use, and its ceilings are policy living in `batch/policy.py`. `reporting` stays out of `batch` because rendering belongs to the CLI that drives a batch, not to the batch itself.

One row came out *wider* instead, and for the same reason read backwards: `cli` reaches `persistence` because `cli/batch.py` is the **composition root** — it decides where the database lives, opens the engine, and hands the session factory down to the scheduler. That is exactly *why* `batch` needs no route to `settings` and never imports a global engine, so the edge is the inversion working rather than leaking. `ARCHITECTURE.md` carries the argument.

`tests/unit/test_layering.py` parses every module's imports with `ast` and asserts each package imports only from its allowed set, so this is structural rather than a matter of discipline. A strategy cannot write to the database, cannot know a batch is running, and cannot behave differently under the scheduler than under the integration CLI. The allowlist is written from the real graph and may only ever shrink. Because each row is the real graph rather than an intention, widening one — say `extraction → catalog` — is a deliberate allowlist edit that surfaces in review, never something a new import does silently. That `extraction` may reach neither `persistence` nor `batch` is asserted by name as well as by table, so the invariant survives a future edit to the allowlist.

### Strategy port

Every run dispatches through `extraction.get_strategy(company.strategy).extract(company, ctx)`. The port is in `src/vacantes/extraction/base.py`: the `ExtractionStrategy` Protocol, frozen `RunContext`, the `STRATEGIES` registry, and `build_report(...)` — the single author of the output shape.

- `strategy="dom"` (default) — `extraction/dom/strategy.py`. browser-use agent under `GOAL_PROMPT` + the deterministic matcher.
- `strategy="greenhouse" | "phenom" | "talentbrew" | "coveo" | "peopleforce" | "bamboohr"` — `extraction/ats/*.py`. API adapters for boards the DOM path cannot reach or cannot regression-test honestly. No browser and no API key, except `coveo` with `browser_token_key` set, which opens a browser session purely to read a token out of page state. The four agent-only report fields emit `None`.

Adding a strategy requires extending the `StrategyName` literal in `domain/company.py` **and** registering it in `extraction/__init__.py` — a unit test asserts the registry matches the literal, so either half missing fails.

### Package layout (`src/vacantes/`)

- `cli/` — `main.py` is the `vacantes` dispatcher (a table of subcommands, not a branch per subcommand); `integrate.py` holds the argparse surface and the per-company orchestration loop; `batch.py` is the batch subcommand and the composition root, exposing `add_batch_arguments`, `select_companies`, `build_policy`, and `execute`. `settings.py` — defaults (model, max steps, render-settle constants shared by runtime and capture).
- `domain/` — pydantic schema: `Company` (with `strategy`, `paginate`, `hooks`, `pre_filter_urls`), `LinkRule` (`path_prefix`, `min_depth`, `suppress_ancestor_selector`), `RuntimeHooks`, `ExtractionResult`, `TargetRegion` (+ the `COSTA_RICA_LATAM` singleton). Cross-field validators fire at catalog-import time (e.g. hooks require `strategy="dom"`; `pre_filter_urls` must share origin with `job_board_url`).
- `catalog/` — the `COMPANIES` list plus `find_company`/`slugify`. The source of truth for the corpus; persistence projects it and never owns it. Handle resolution iterates in list order, checking alias > acronym > substring — insertion order is authoritative when names collide.
- `extraction/dom/` — `assets/collect_links.js` is the matcher's **single source of truth** (loaded via `importlib.resources` as `EXTRACT_JOB_LINKS_JS`; edit the JS, never an inlined copy). `collector.py` runs it against a live page and owns the pagination walker and hook execution behind the `PageDriver` protocol.
- `extraction/dom/agent/` — the agent wiring, which lives here because `extraction/dom/strategy.py` is its only consumer: `prompt.py` (`GOAL_PROMPT` clause constants + `build_goal_prompt(region)`), `runner.py` (agent/session build), `controller.py` (registers the `extract_job_links` tool).
- `reporting/output.py` — JSON output to `./output/` (gitignored) + terminal summary. Kept top-level rather than CLI-private because both `integrate` and batch's optional JSON output render through it.

### The database

`persistence/` holds `engine.py` (the connection URL built from `settings.DATABASE_PATH`, the four SQLite pragmas, `create_session_factory`, a `database()` context manager, and `missing_tables` for the schema preflight), `models.py` (the three tables), `timestamps.py`, and three repository modules: `catalog_repo` (`sync_catalog`), `jobs_repo` (`replace_company_urls`, `list_company_urls`), and `runs_repo` (`start_run`, `finish_run`, `fail_run`, `has_fresh_success`, `reap_stale_runs`). Every SQL statement in the system lives in one of those three, so transaction boundaries and error translation have exactly one home.

Four schema rules are load-bearing; `ARCHITECTURE.md` carries the reasoning behind them. `job_urls` answers *what is open* and `company_runs` answers *did we successfully look, and when*, so they are separate tables and must stay separate. `companies` is a projection of `catalog.COMPANIES` that never prunes an entry which left the catalog, because deleting a row cascades into its URLs and runs. Freshness keys on successes only, which keeps failures eligible for retry and makes crash recovery a consequence of the rule rather than a mechanism of its own. A successful extraction replaces a company's URL set by delete-and-insert inside one transaction, so a crash leaves either the previous snapshot or the new one.

Two of the four pragmas are correctness rather than tuning. Without `busy_timeout`, concurrent workers produce intermittent `database is locked` errors; without `foreign_keys=ON`, SQLite ignores every `REFERENCES` clause and the `ON DELETE CASCADE` declarations become documentation.

Every timestamp crossing the persistence boundary is **naive UTC**, normalized at the repository boundary by `timestamps.py`. SQLite stores a `DateTime` as an ISO-8601 string and compares it lexicographically, so an aware value's `+00:00` suffix would order incorrectly against a naive one and misjudge the freshness predicate with nothing failing loudly.

`slugify` lives in `domain/company.py` and is re-exported from `catalog` for its existing callers — persistence needs it and has no route to `catalog`. Prefer `Company.slug` when holding an entity: it is a derived property, so the `companies` primary key, the output filename, and the snapshot directory are the same string by construction.

### The batch

`batch/` is three modules with one responsibility each. `policy.py` decides *whether and how expensively*: `concurrency_class(company)`, the frozen `BatchPolicy` (the two ceilings, the freshness window, the per-company timeout, and `force`), and `should_skip`. `worker.py` decides *what one unit of work means*: `run_company` and the frozen `CompanyOutcome` it resolves to. `scheduler.py` decides *when*: `new_batch_id`, `run_batch`, and the frozen `BatchResult` that aggregates the outcomes. The whole component is a semaphore and a gather, not a workflow framework.

Four decisions in that shape are structural; `ARCHITECTURE.md` carries the reasoning behind them. Concurrency is classified by **cost** rather than strategy name, which is why `concurrency_class` takes a `Company` — a `coveo` entry with `browser_token_key` set opens a real browser and belongs in the browser bucket despite being an API strategy, and a test asserts every key in `STRATEGIES` has a declared class so an eighth adapter cannot ship unclassified. The two ceilings are independent semaphores rather than one shared one. `worker.py` is the failure-isolation boundary, where a broad `except Exception` keeps one board from aborting the batch while `KeyboardInterrupt`, `SystemExit`, and `CancelledError` still propagate, and where an extraction returning zero jobs is a successful run with an unhappy verdict rather than a failure. Crash recovery needs no checkpoint file because it falls out of the freshness rule, with the per-company timeout doubling as the staleness bound the scheduler reaps against at startup.

Two rules are this file's to state. Each repository call gets its **own short-lived session**: the repositories each open their own transaction, so a session that has already autobegun one for a read cannot begin another, and sharing one across two calls raises `InvalidRequestError`. The same rule keeps a session from being held across the minutes-long `extract` await, where it would pin a pool connection and potentially a SQLite write lock. Separately, the per-company timeout lives on `BatchPolicy` and not on `RunContext` — `RunContext` is the shared port both entry points pass through, and a batch-only concern has no business widening it.

Two facts to have before debugging a batch, both settled by killing a live one rather than by reasoning. Recovery does not *depend* on the reap: an `in_progress` row is not a success, so the company is eligible again the moment the next batch looks, and the reap only closes the abandoned row once the staleness bound has passed. And a kill does not invalidate an earlier success — a company that succeeded inside the freshness window is correctly skipped right after a crash, because nothing is owed for it.

`CompanyOutcome.url_count` carries what `replace_company_urls` actually stored rather than re-deriving the number from the report, so the printed total is the one `select count(*) from job_urls` reproduces; a board that emits the same posting twice would otherwise be over-counted. `worker.py` also logs one line per company at INFO — a skip, a success with its stored count and verdict, or a failure with its message — because a full-corpus run takes hours and an operator needs to watch boards resolve as they go.

`cli/batch.py` preflights the schema through `persistence/engine.missing_tables`, which lives in `persistence/` because nothing outside that package may build a query, and in `engine.py` specifically because it asks about the database as an artifact rather than about any table's rows. Without it the first `vacantes batch` on a fresh checkout would die with an `OperationalError` from inside a repository instead of naming the `alembic upgrade head` the operator actually owes. It compares against `models.Base.metadata`, so Alembic's own `alembic_version` is not counted.

### Per-company escape hatches (all inert by default)

When the agent fails a board deterministically, pin the fix on the deterministic side rather than prompt-tweaking per site: `paginate=True` (multi-page walker), `hooks.pre_extract_css` / `hooks.expand_selector` / `hooks.next_control_selector` / `hooks.filter_already_applied`, `pre_filter_urls` (agent-less union over URL variants), `LinkRule.min_depth` / `suppress_ancestor_selector`. Each default is byte-identical to the pre-feature code path. Decision guidance and the motivating board for each knob are in `TABNINE.md`.

### Testing model

Two independent regression corpora, don't conflate them:

- **DOM companies**: frozen page copies under `tests/fixtures/snapshots/<slug>/` (`page.html` + `metadata.json`, optionally `frames/`, `pages/`, `states/`). `tests/snapshots/test_extractor_snapshots.py` replays the production matcher JS in real Chromium and asserts the recorded **unfiltered** count — this validates the matcher, not the agent's filtering. Captures come from `scripts/capture_snapshot.py -c <handle> --expected N` (pass `--paginate` iff the entry has `paginate=True`).
- **API companies**: recorded payloads under `tests/fixtures/api/<ats>/` with respx-mocked tests in `tests/unit/`. Greenhouse filenames are the board *token*, not the company slug.

`tests/snapshots/test_linkrule_parity.py` pins the Talentbrew Python URL-bucketing mirror to the shipped JS matcher — matcher edits must keep both in sync.

Persistence tests (`tests/unit/test_persistence.py`) run against a **real SQLite file** under `tmp_path`, never `:memory:` — an in-memory database exercises neither WAL nor `busy_timeout`, which would make the pragma assertions vacuous. Repository tests build the schema from `Base.metadata` so they fail for their own reason; `TestMigrationParity` in the same file closes the resulting gap by applying `alembic upgrade head` to a temporary database and asserting Alembic's own `compare_metadata` finds no difference against `models.Base.metadata`. That is what catches a model field added without a migration.

Batch tests (`tests/unit/test_batch.py`) use no browser and no network. A fake strategy is registered over an entry in `STRATEGIES` with `monkeypatch.setitem`, and the real scheduler, worker, and repositories then run against a real SQLite file. The fake records its own per-class high-water mark, so a concurrency ceiling is *observed* rather than assumed, and it gates on an explicit target — each extraction blocks until the expected number are simultaneously live — because simply sleeping made the assertion a race against however long the workers spent in the database and under-counted on a fast machine.

`tests/unit/test_cli_batch.py` covers the subcommand on the same terms, driving the real command against a temporary database with a fake strategy. Every argument namespace in it comes out of `cli.main.build_parser` rather than being hand-built, so an unregistered subcommand or a renamed flag fails there rather than in a manual run.

The repo does not use `pytest-asyncio`. Async tests are driven on a background thread with a fresh event loop by `tests/unit/asyncio_harness.py`, which exposes `run_async`, `run_in_thread`, and `track_engine`. The harness exists because the snapshot suite drives Chromium through Playwright's sync API and leaves a running-loop registration on the main thread's `asyncio.events` state that makes both `asyncio.run` and a main-thread `run_until_complete` raise for the rest of the session; `tests/unit/test_prefiltered.py` carries the original diagnosis. It also owns engine disposal: an async engine outliving its loop leaves aiosqlite holding a closed one and the error surfaces in whichever test runs next, so `run_async` disposes every engine passed to `track_engine` on the loop that created it.

### Adding a company

The canonical workflow is the `integrate-company` skill, driven by the untracked `new-companies.json` queue (schema in `new-companies.example.json`). First step is always `scripts/probe_board.py` (read-only reconnaissance: anchor census, frame/shadow map, pagination affordances, platform fingerprint, suggested `Company(...)` snippet). Then: ground-truth verification (`.claude/skills/integrate-company/scripts/extractor_ground_truth.py`), live agent run, snapshot capture, quality gates, one `feat:` commit. `expected_jobs` in the queue is a human-counted target — never adjust it to match observed output.

## Conventions

- Python 3.12+, uv-managed. Ruff (line-length 88, double quotes, rules `E,F,I,W,UP,B,C4,SIM`); mypy strict (`disallow_untyped_defs`) — every function fully annotated.
- Import as `from vacantes.X`; the package lives under `src/`.
- `extraction/dom/agent/prompt.py` and `tests/unit/test_prompt_render.py` are E501-exempt on purpose: prompt clauses and their golden copies are byte-exact single-line literals — never reflow them.
- `TABNINE.md` is the Tabnine CLI's guidance file; its MCP-only tool-routing rule applies to Tabnine, not Claude Code. Keep the two files' project facts consistent when architecture changes.
- The `integrate-company` skill is maintained in two copies, `.claude/skills/` and `.tabnine/agent/skills/`. They are identical except for two deliberate per-assistant adaptations: the Playwright MCP tool prefix and the guidance file each cites for the commit convention. Keep every other change in sync across both.
