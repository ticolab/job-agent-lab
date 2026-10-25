# TABNINE.md

Guidance file for the Tabnine CLI when working in this repository. Keep this file's project facts consistent with `CLAUDE.md`; both are guidance surfaces for different AI assistants and neither should drift from the other when the architecture changes.

## Project Overview

`job-discovery` answers one question a few times a week: **what job postings are open right now.** It extracts job-posting URLs from a corpus of real career sites, and it exists to stress-test one design decision — **navigation is non-deterministic, extraction is deterministic** — against a large, diverse corpus before the approach is trusted in a production pipeline.

For each configured company, a `browser-use` agent (OpenAI model routed via LiteLLM) drives the career page: loads the board, applies a location filter, reveals lazily-mounted content, and expands collapsed sections. The agent never decides what counts as a job link — that judgement belongs to a single JavaScript matcher (`collect_links.js`) which runs inside the page via `page.evaluate` and returns same-origin `<a>` tags matching a per-company path prefix. A regression harness replays the shipped matcher against frozen page snapshots so counting behaviour can be validated offline in seconds across the whole corpus.

Only one of the seven registered strategies drives an agent at all; the other six are plain HTTP against the ATS or search platform backing the board. "Agent" therefore names a mechanism inside `extraction/dom/`, not the system.

Read `ARCHITECTURE.md` for the design rationale and invariants. Read `blockers/INTEGRATION_BLOCKERS.md` for the catalogue of site behaviours that resist extraction (referenced as C1–C24 throughout the codebase). Read `CLAUDE.md` for the equivalent guidance surface aimed at Claude Code.

Job processing adds three sibling packages. The dependency-free `job_eligibility` and `job_extractor` leaves render prompts and validate grounded responses. `job_pipeline` owns identity, content acquisition, model calls, persistence, lifecycle, the runner, and the implemented `preview`, `run`, `reprocess`, and `show` commands. The live posting corpus, operator-reviewed expectations, and production model rollout remain deferred; implemented code and offline tests are not evidence that those rollout steps occurred.

## Building and Running

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). All commands run through `uv`.

Setup the environment and Chromium (needed for both the live agent and the snapshot regression suite):

```bash
uv sync --extra dev                  # runtime + dev deps
uv sync --group test                 # pytest, pytest-playwright, respx
uv run playwright install chromium
cp .env.example .env                 # add OPENAI_API_KEY for live DOM runs
```

Three console scripts are registered. `job-discovery` dispatches `integrate` and `scan`; `job-agent-lab` remains the integrate alias. `job-pipeline` implements `preview`, `run`, `reprocess`, and `show`. `preview` observes by default and reports dispositions without constructing HTTP, browser, or model clients; `--no-observe` is read-only. `run --dry-run` reuses preview, while `run` processes current-snapshot `new` and due `retry` jobs. `reprocess` requires an audit reason and resumes explicit jobs, or one company's unknown jobs, from a selected checkpoint. `show` prints audit metadata and accepted structured results without stored posting bodies.

Run the extractor end-to-end. The `-c` flag matches by alias, then acronym, then substring against `catalog.COMPANIES`:

```bash
uv run job-agent-lab                                        # every company
uv run job-agent-lab -c akurey                              # one company
uv run job-agent-lab -c gap --headed --model gpt-4o --max-steps 25
uv run job-discovery integrate -c akurey                         # identical, via the dispatcher

uv run job-pipeline --help
uv run job-pipeline preview --all --by-company
uv run job-pipeline run --all
uv run job-pipeline reprocess --job 42 --reason "policy review"
uv run job-pipeline show --job 42
uv run python scripts/verify_grounding.py
sqlite3 data/job-discovery.db < scripts/pipeline_queries.sql
```

Results are written as timestamped JSON to `./output/` (gitignored) and a summary is printed to the console. Runtime reports observations and has no verdict field or `--strict` mode.

`OPENAI_API_KEY` is required for live `strategy="dom"` discovery runs and for `job-pipeline run` or `reprocess` with an OpenAI model. Anthropic models require `ANTHROPIC_API_KEY`. Pipeline model commands load `.env` and preflight recognized credentials before observing, preparing, or claiming work; a missing key exits 2 without consuming work. `preview`, `show`, unit tests, snapshot tests, replay callers, API discovery strategies, and `pre_filter_urls` companies need no provider credential.

Format, lint, type-check, and test:

```bash
uv run ruff format . && uv run ruff check . --fix
uv run mypy                                      # src, scripts, tests, migrations
uv run --group test pytest tests/                # full suite
uv run --group test pytest tests/unit/           # browser-free
uv run --group test pytest tests/snapshots/      # needs Chromium
```

Pre-commit gates ruff, mypy, and the full test suite on any change under `src/`, `migrations/`, `scripts/`, `tests/`, or either `integrate-company` skill copy, and on any change to `pyproject.toml` or `uv.lock`. The trigger covers the whole package rather than a couple of subpackages because anything narrower silently exempts the rest: while it named only `extraction` and `domain`, a code-only change to `cli/`, `batch/`, or `persistence/` ran no tests at all. The ruff and mypy hooks run through `uv run`, so they use the versions pinned in `uv.lock` — the same ones the commands above and CI use — and the mypy hook checks the whole `files` set in `pyproject.toml` rather than only the staged files, so a green `uv run mypy` means the hook passes too.

CI (`.github/workflows/ci.yml`) runs on every pull request and on `main`: `static` (the pre-commit hooks other than the test hook, plus a `uv.lock` freshness check), `unit`, `snapshots` (Chromium on Linux), and `package`. That last job exists because every test imports the package from an editable install with the test group present: it installs the built wheel into a clean environment on Linux and macOS, loads both JS assets, checks that every module the package imports — including imports inside functions — is installed, runs all three console scripts, migrates a fresh database up, down, and up again, requires a single Alembic head, and plans a `scan --dry-run --all`. CI holds no secrets and must stay that way — the suite is designed to pass without `OPENAI_API_KEY`, and a live agent run is never a merge gate. The ruleset on `main` requires only the `ci-ok` aggregate (plus `pr-title`), so a job can be added or renamed without touching it. Never put `paths:` filters on that workflow: a required check whose workflow is skipped never reports, and the pull request waits forever. uv is pinned in the workflow to the version used locally; bump it there when upgrading uv.

Alembic owns the schema. The gitignored database defaults to `data/job-discovery.db`. Discovery's current URL snapshot is regenerable by scanning again, but pipeline jobs, content checkpoints, attempts, decisions, and accepted results are durable audit history; back up the file if that history matters:

```bash
uv run alembic upgrade head                        # create or migrate the database
uv run alembic revision --autogenerate -m "..."    # new revision from models.py
sqlite3 data/job-discovery.db 'select * from job_urls'  # query it directly
```

The connection URL is built by `migrations/env.py` from `settings.DATABASE_PATH`; `sqlalchemy.url` in `alembic.ini` is deliberately empty so the path has one source of truth. The path defaults to `data/job-discovery.db` and is overridden by the `JOB_DISCOVERY_DB` shell variable (not a `.env` key: Alembic never loads `.env`). `job-discovery scan --database PATH` points one scan elsewhere, but Alembic has no such flag, so a non-default database is migrated with `JOB_DISCOVERY_DB=PATH uv run alembic upgrade head`; the schema preflight prints that exact command when it applies.

`job-discovery scan` is the only writer of the discovery URL snapshot; `job-pipeline` writes the processing and audit tables. Both preflight the migrated schema. A scan selection is mandatory — there is no bare invocation that means "the whole corpus" — because a full scan drives hundreds of boards, many of them paying for a browser and an LLM:

```bash
uv run job-discovery scan --dry-run --all                  # plan only, writes nothing
uv run job-discovery scan -c speechify -c cloudbeds        # two boards
uv run job-discovery scan --all --exclude accenture        # everything but one
uv run job-discovery scan --companies queue.txt --force    # a file of handles, ignore freshness
```

Handles resolve exactly as `integrate -c` resolves them, and unknown ones abort before any work starts. `--dry-run` is the first thing to reach for when a scan misbehaves: it prints which companies would run, which are skipped as fresh, and the concurrency ceilings, without writing even the catalog projection. A completed scan exits zero even when individual boards failed, matching `integrate`'s default; query `company_runs` for board health rather than reading the exit code.

## Architecture

### Four package roots

The root dependency table is enforced by `tests/unit/test_layering.py`:

```
job_pipeline    → job_eligibility, job_extractor,
                  job_discovery.domain, job_discovery.catalog,
                  job_discovery.persistence, job_discovery.settings
job_eligibility → nothing in the repository
job_extractor   → nothing in the repository
job_discovery   → neither leaf nor job_pipeline
```

The leaves are pure: text in, prompt/schema/validation out. They never call a model, open a browser, or touch persistence. `job_pipeline` owns every side effect and makes a small eligibility call before extraction, so ineligible and unknown jobs never pay for full extraction. Both calls use the same stored text.

Pipeline models share discovery's `Base` and Alembic chain. Their company foreign key uses `RESTRICT`, preserving durable history when the catalog changes. Ordinary runs process current-snapshot jobs in `new` or due `retry`, leave `in_progress` alone, and skip `completed_eligible`, `completed_ineligible`, `unknown`, `unavailable`, `failed`, and `merged`. Unknown is terminal unless explicitly reprocessed.

The runner reaps stale unfinished attempts, observes discovery URLs into company-scoped logical jobs, plans eligible work, and processes jobs concurrently under independent HTTP, browser, model, and per-host ceilings. `current_attempt_id` is the ownership lease: phase advances, checkpoints, merges, and releases require the active attempt, so a stale worker cannot overwrite newer state. Stored content, accepted eligibility, and accepted extraction are durable checkpoints. Retry resumes from `next_phase`; explicit reprocessing can restart from `fetch`, `eligibility`, or `extraction` and invalidates only downstream current records. There is no external checkpoint file.

Three timing rules keep a corpus-sized run honest. `--timeout-seconds` bounds each network or model step *once it holds its slot*, never the time a job spends queued for one; a whole-lifecycle ceiling would time out most of a 1,500-job first run while it waited for ten fetch slots and four model slots, burn an attempt per job, and repeat daily until terminal. The scheduler claims at most `http + model` concurrency jobs at a time, so an attempt's age tracks its active time. And the reaper's staleness bound is four timeouts (`STALE_ATTEMPT_PHASES`), one per timed step an attempt can hold, rather than the timeout itself. Ctrl-C cancels the run's task and waits for it to unwind before exiting 130; the next run's reaper releases whatever was in progress.

Duplicate merging is conservative because a merge cannot be undone from the CLI. A JSON-LD requisition id counts only when it is the `PropertyValue` `value` (never the organization `name`), exactly one other live job carries it, and the two titles agree, so a board that stamps every posting with one identifier never collapses into one job. The content fingerprint hashes the whole normalized text, not a prefix, so two roles behind a long shared preamble stay distinct. A duplicate another worker is processing is left for that worker. A redirect may land on a sibling subdomain or a known ATS host; anything else is off-site. A past JSON-LD `validThrough` closes a posting only when the page corroborates it with a closure phrase or a body too short to be open.

Each pipeline repository call gets its own short-lived session. Never hold a session across network, browser, or model awaits, and do not reuse a session between repositories that own their transactions.

The live posting corpus and reviewed expectations remain rollout work. When populated, the golden workflow uses frozen posting text, `scripts/draft_expectations.py`, operator review, and `scripts/evaluate_leaf.py`. Unreviewed drafts are not truth. Live model comparison and model rollout are manual operations and never secret-bearing CI gates.

Prompt or response-schema changes require the matching `POLICY_VERSION` or `EXTRACTOR_VERSION` bump and prompt-hash golden update. A model-only change does not bump either version. Versions do not trigger automatic reprocessing. The configured `gpt-4.1-mini` default does not mean that model has passed corpus review or been rolled out.

Technology normalization belongs in `job_extractor/normalize.py` and its packaged alias table, never in the prompt. Preserve source mentions and evidence.

### Discovery components and the shared kernel

Three components sit over one shared kernel. `extraction/` owns the port and every strategy. `persistence/` owns the database. `batch/` owns concurrency and the per-company unit of work. The kernel — `domain/`, `catalog/`, `settings.py` — is the vocabulary all three speak.

All three components exist today, alongside `cli/` and `reporting/`, and both subcommands are wired: `job-discovery integrate` drives one board at a time and `job-discovery scan` drives the corpus.

Every SQL statement in the system lives in one of `persistence/`'s three repository modules (`catalog_repo`, `jobs_repo`, `runs_repo`), so transaction boundaries and error translation have exactly one home. `engine.py` owns the connection URL and the four SQLite pragmas; `models.py` owns the three tables.

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
  domain      → (nothing inside job_discovery)
```

Two rows are narrower than initially anticipated, and both for the same reason: a component that is *handed* what it needs does not import it. `persistence` does not reach `catalog` because `sync_catalog` receives the companies to write as an argument rather than importing `COMPANIES`. `batch` does not reach `catalog` or `settings` either — the scheduler is given both the companies to run and the session factory to use, and its ceilings are policy that lives in `batch/policy.py`. `reporting` stays out of `batch` because rendering belongs to the scan CLI that invokes a batch, not to the batch itself.

The `cli → persistence` edge is that same principle seen from the other end. Something has to decide where the database lives and open the engine, and that job belongs to the outermost layer: `cli/scan.py` is the composition root, which is *why* `batch` can be handed a session factory instead of importing one.

`tests/unit/test_layering.py` parses every module's imports with `ast` and asserts each package imports only from its allowed set, which makes this structural rather than a matter of discipline. A strategy cannot write to the database, cannot know a batch is running, and cannot behave differently under the scheduler than under the integration CLI. The allowlist is written from the real graph, covers the packages actually present, and may only ever shrink. Because each row is the real graph rather than an intention, widening one — say `extraction → catalog` — is a deliberate allowlist edit that surfaces in review, never something a new import does silently. That `extraction` may reach neither `persistence` nor `batch` is asserted by name as well as by table, so the invariant survives a future edit to the allowlist.

The reason this matters is that two entry points share one core. The `integrate` CLI iterates sequentially and writes a JSON artifact a human reviews before committing a catalog entry; the batch scheduler runs the corpus concurrently and persists the current URL set. Both call the same `extract` coroutine with the same `RunContext`. If they could diverge, the integration workflow would stop being a correctness signal for scheduled runs.

### The central split

Every run dispatches through one port: `extraction.get_strategy(company.strategy).extract(company, ctx)`. The port is defined in `src/job_discovery/extraction/base.py` and consists of the `ExtractionStrategy` Protocol, the frozen `RunContext` (model, headless flag, step cap, target region), the `STRATEGIES` registry, and `build_report(...)` — the single author of the output shape.

Two families implement the port. `strategy="dom"` (the default) runs the browser-use agent under `GOAL_PROMPT` alongside the deterministic matcher. The six API adapters — `greenhouse`, `phenom`, `talentbrew`, `coveo`, `peopleforce`, `bamboohr` — live under `extraction/ats/` and bypass the browser entirely, querying the ATS or search platform that backs the board and filtering the returned records by region. Each API strategy earns its module when the DOM path cannot yield an honest regression artifact or cannot reach the postings at all. The one exception to "no browser" is `coveo` with `browser_token_key` set, which opens a browser session purely to read a search token out of page state.

Adding a new strategy requires extending the `StrategyName` literal in `domain/company.py` **and** registering the implementation in `extraction/__init__.py`. A unit test asserts the two sets are equal, so shipping half the change fails loudly.

### Package layout

The `src/job_discovery/` tree is organized so each package owns exactly one concern. The CLI lives in `cli/`: `main.py` is the `job-discovery` dispatcher (a table of subcommands rather than a branch per subcommand), `integrate.py` holds the argparse surface and the per-company orchestration loop, and `scan.py` exposes the scan subcommand and invokes the internal batch scheduler. Runtime defaults live in `settings.py` (`DEFAULT_MODEL`, `DEFAULT_MAX_STEPS`, `OUTPUT_DIR`, plus the render-settle constants shared by runtime and capture).

The `domain/` package holds the pydantic schema: `Company` with its `strategy`, `paginate`, `hooks`, and `pre_filter_urls` knobs; `LinkRule` with `path_prefix`, `min_depth`, and `suppress_ancestor_selector`; `RuntimeHooks`; `ExtractionResult`; and `TargetRegion` together with the `COSTA_RICA_LATAM` singleton. Cross-field validators fire at catalog-import time — hooks require `strategy="dom"`, `pre_filter_urls` must share origin with `job_board_url`, and API strategies require or forbid their tenant config exactly as documented in each class docstring.

The `catalog/` package supplies the `COMPANIES` list plus `find_company`/`slugify` helpers. It is the source of truth for the corpus; the persistence layer projects it and never owns it. Handle resolution iterates in list order, checking alias, then acronym, then substring; insertion order is authoritative when names collide.

The `extraction/dom/` package owns the deterministic matcher. `assets/collect_links.js` is the single source of truth — loaded once at import time via `importlib.resources` as `EXTRACT_JOB_LINKS_JS`. Edit the JS, never an inlined copy. `collector.py` runs it against a live page and owns the pagination walker and hook execution behind the `PageDriver` protocol.

The `extraction/dom/agent/` package holds the agent wiring, and it lives under `extraction/dom/` because `extraction/dom/strategy.py` is its only consumer: the prompt in `prompt.py` (`GOAL_PROMPT` clause constants plus `build_goal_prompt(region)`), the agent/session build in `runner.py`, and the tool registration in `controller.py` (which registers the `extract_job_links` tool the agent calls).

The `reporting/output.py` module writes JSON output to `./output/` and prints the terminal summary. It never authors the report shape itself; that lives in `build_report`. It stays top-level rather than CLI-private because both `integrate` and scan's optional JSON output render through it.

### The database

Three tables, and the split between two of them is load-bearing. `job_urls` answers *what is open*; `company_runs` answers *did we successfully look, and when*. A single `updated_at` column spanning both would make a failed run indistinguishable from a successful one, so a board that broke this morning would look "done" this afternoon and would silently stop being retried. `companies` is a third table and a pure projection of `catalog.COMPANIES`, refreshed at the start of every scan so the database is self-describing; it is never an owner, and rows for companies removed from the catalog are deliberately left in place because deleting one would cascade into its URLs and runs.

The freshness rule keys on **successes only** (`status = 'success'`), which is what keeps failures eligible for retry and successes from being repeated inside the window. Crash recovery then falls out of that rule rather than needing a mechanism of its own: a killed scan leaves `in_progress` batch rows, the next scan reaps them into `failed`, and because they are not successes the affected companies re-run while genuinely-completed ones are skipped. No checkpoint file exists.

A successful extraction replaces a company's URL set wholesale — delete-and-insert inside one transaction. No history is kept by design, and this cannot leave a stale posting behind the way upsert-plus-prune can. The transaction is the failure boundary: a crash before, during, or after leaves either the previous snapshot or the new one, never a partial set.

Two of the four SQLite pragmas in `engine.py` are correctness, not tuning. Without `busy_timeout`, concurrent workers produce intermittent `database is locked` errors; without `foreign_keys=ON`, SQLite ignores every `REFERENCES` clause and the `ON DELETE CASCADE` declarations become documentation. A unit test asserts all four on a real file, which is why the persistence tests never use `:memory:` — an in-memory database exercises neither of those two settings.

Every timestamp crossing the persistence boundary is **naive UTC**, normalized at the repository boundary by `persistence/timestamps.py`. This is an invariant rather than a convention: SQLite stores a `DateTime` as an ISO-8601 string and compares lexicographically, so an aware value's `+00:00` suffix would order incorrectly against a naive one and misjudge the freshness predicate with nothing failing loudly.

`slugify` lives in `domain/company.py` and is re-exported from `catalog` for its existing callers. Prefer `Company.slug` when holding an entity: it is a derived property, so the `companies` primary key, the output filename, and the snapshot directory are the same string by construction.

### The batch

Three modules with one responsibility each. `policy.py` decides *whether and how expensively*, `worker.py` decides *what one unit of work means*, `scheduler.py` decides *when*. The whole component is a semaphore and a gather, which is the right weight for one operator, one machine, and units of work with no dependencies between them.

Concurrency is classified by **cost**, not by strategy name, which is why `concurrency_class` takes a `Company` rather than a string. Splitting on the name gets exactly one case wrong: a `coveo` company with `browser_token_key` set opens a real browser to read the JWT its own page minted into `sessionStorage`, so it costs a Chromium context despite being an API strategy. Twenty of those in the HTTP bucket would exhaust memory. A test asserts every key in `STRATEGIES` has a declared class, so an eighth adapter cannot ship without someone deciding what it costs.

The two semaphores are independent on purpose. A browser run holds a Chromium context and, for `dom`, an LLM agent bounded by provider rate limits; an HTTP run is a JSON call bounded only by politeness. One shared ceiling would make the cheap bucket queue behind the expensive one.

`worker.py` is the isolation boundary, and the broad `except Exception` there is correct rather than lazy: failure modes across 250 third-party sites are open-ended, and the contract is that no single board aborts the batch. `KeyboardInterrupt`, `SystemExit`, and `CancelledError` derive from `BaseException` and still propagate. An extraction returning zero jobs is a **successful observed run**, never a failure — marking it failed would put honestly-empty boards in the retry path forever and hide real breakage among them.

The per-company timeout lives on `BatchPolicy`, not on `RunContext`. `RunContext` is the shared port both entry points pass through, and a batch-only concern has no business widening it.

Each repository call gets its **own short-lived session**. Pipeline repositories each open their own transaction, so a session that has already autobegun one for a read cannot begin another — sharing a session across two repository calls raises `InvalidRequestError`. The same rule keeps a session from being held across network, browser, or model awaits, where it would pin a pool connection and potentially a write lock.

Crash recovery needs no checkpoint file. A killed scan leaves `in_progress` batch rows; the next scan reaps them to `failed` at startup, using the per-company timeout as the staleness bound since no run may legitimately exceed it; they are therefore not successes, so the freshness rule re-runs exactly those companies and skips the ones that completed.

Two consequences of that design are worth knowing before debugging a scan, both observed live rather than reasoned about. **Resumption does not depend on reaping**: an `in_progress` row is not a success, so the company is eligible again immediately, and the reap is only the bookkeeping that closes the abandoned batch row once the staleness bound has passed. **A killed scan does not invalidate an earlier success**, either — so a company whose previous run succeeded inside the freshness window is correctly *skipped* right after a kill, because nothing is owed. And because the staleness bound *is* `--timeout-seconds`, lowering it to reap sooner also shortens every run's ceiling; the flag's help says so.

Concurrency at the default ceiling changes no board's count. After the first full scan, every deterministic count mismatch against then-current onboarding evidence and every empty agent board was re-run alone through `integrate` and reproduced the scan's number exactly. This shows the scheduler did not degrade extraction. Runtime health comes from observed `status`, `url_count`, errors, and timestamps; investigate successful runs with zero URLs stored.

`cli/scan.py` preflights the schema before doing anything, because connecting to a SQLite path that does not exist creates an empty file quite happily. Without the check, the first ever `job-discovery scan` on a fresh checkout would die with an `OperationalError` from inside a repository instead of naming the `alembic upgrade head` the operator actually owes. `persistence/engine.missing_tables` answers that question, and it lives there rather than in the CLI because nothing outside `persistence/` may build a query.

### The deterministic matcher

The matcher scans the top document, descends into every open shadow root, and enters every same-origin frame it can reach — guarded so inaccessible frames are skipped rather than fatal. Each surviving anchor is gated on CSS visibility. An anchor must be same-origin and match one of two URL shapes: the id sits in the path (`/jobs/12345-engineer`), or the id sits in the query on the prefix itself (`/careers/requirements/?pId=180`). When both shapes appear on one page the path bucket wins and the query bucket is discarded, because a query on a listing root is usually a filter facet rather than a posting.

Boards that need more discrimination get it through data, never through a site-specific code branch. `LinkRule.path_prefix` overrides the derived prefix, `LinkRule.min_depth` requires the id-in-path tail to be at least N segments deep, and `LinkRule.suppress_ancestor_selector` drops anchors inside a named container. Each field defaults to a value that leaves matcher behaviour byte-identical.

### Per-company escape hatches

When the agent fails a board deterministically, pin the fix on the deterministic side rather than prompt-tweaking per site. Every knob defaults to a value byte-identical to the pre-feature code path. The `paginate` boolean turns on the multi-page walker. The six `RuntimeHooks` fields (`pre_extract_css`, `expand_selector`, `next_control_selector`, `filter_already_applied`, `render_wait_sec`, `render_scroll_count`) each address a specific board's failure mode with decision guidance and motivating board named in the field's docstring. The two settle fields override the global render wait and scroll count for a board whose listing hydrates too slowly for them — Edwards Lifesciences shows no job anchors until ~50s — and a cross-field validator restricts them to entries with non-empty `pre_filter_urls`, because the agent-less runner is the only path that applies a settle and an override elsewhere would move the captured fixture without moving the runtime. The `pre_filter_urls` tuple runs an agent-less union over URL variants for boards whose location filter can only be applied via URL. Decision guidance for each knob lives inline in `domain/company.py`.

The agent's `extract_job_links` tool carries a posting-page guard (`extraction/dom/rules.is_posting_url`): it refuses, with an error the agent can act on, when the current page's own URL would be collected as a posting by the matcher's id-in-path rule — the shape of an agent that has clicked into a job and asked for "all listings" there, which otherwise returns a plausible-looking 1. The id-in-query shape is not treated as a posting (filtered listing roots look identical) and the board's own listing path is exempt; both exclusions are pinned corpus-wide in `tests/unit/test_posting_guard.py`. Agent-less `pre_filter_urls` boards never invoke the tool.

The capture-side scripts launch through `settings.launch_capture_browser`: a real-Chrome channel plus `--disable-blink-features=AutomationControlled`, with the UA read from the launched instance so it cannot advertise a different build than the engine running. Two boards need it — Edwards detects the automation surface, McKinsey refuses the bundled Chromium with `ERR_HTTP2_PROTOCOL_ERROR`. Chrome absent means a warned fallback, not a failure. `settings.navigation_timeout_ms(wait_s)` scales the `page.goto` ceiling with the configured settle, because a board slow enough to need a raised settle can also miss Playwright's 30s `load` default on a cold load.

The region predicate (`domain/region.COSTA_RICA_LATAM.matches`) is text matching, not geography: a location counts only if its string names `costa rica`, `latam`, `latin america`, `CR`/`CRI`, or one of the unambiguous provinces `Heredia` / `Alajuela` / `Cartago` / `Guanacaste` / `Puntarenas`. The provinces were admitted because boards write `Heredia, Heredia, 400803` with no country, and for Greenhouse and BambooHR — which expose no server-side facet — this predicate is the only region signal. `San José` is excluded on measurement: it is Zscaler's California HQ and admitting it adds 58 phantom postings to that fixture. Phenom needs neither, because its server facet is authoritative.

## Testing model

Two independent regression corpora exist, and they must not be conflated.

DOM-strategy companies contribute frozen page copies under `tests/fixtures/snapshots/<slug>/` with `page.html` plus `metadata.json`, optionally with `frames/`, `pages/`, or `states/` subdirectories (a state may carry its own `states/state-N-frames/` and, for declaring boards that also paginate, `states/state-N-pages/` sidecars — Accenture is the first). `tests/snapshots/test_extractor_snapshots.py` replays the production matcher JS in real Chromium and asserts the recorded **unfiltered** count. This validates the matcher's counting, not the agent's filtering. Replays run offline: `tests/snapshots/conftest.py` disables page JavaScript and answers every network request with `204`, so a replay never waits on the live site or follows a navigation the captured page starts itself.

API-strategy companies contribute recorded payloads under `tests/fixtures/api/<ats>/` with respx-mocked tests in `tests/unit/`. For Greenhouse, filenames are the board *token*, not the company slug.

Fixtures freeze the **unfiltered** listing. `expected_jobs` stays in the onboarding queue and is compared externally with the saved live report; it never enters `Company`, runtime reports, or the database. Conflating queue evidence with fixture counts would produce failures that localize to neither.

`tests/snapshots/test_linkrule_parity.py` pins the Talentbrew Python URL-bucketing mirror to the shipped JS matcher — matcher edits must keep both in sync.

Persistence tests (`tests/unit/test_persistence.py`) run against a **real SQLite file** under `tmp_path`, never `:memory:`, because an in-memory database exercises neither WAL nor `busy_timeout` and would make the pragma assertions vacuous. Its `TestMigrationParity` class applies `alembic upgrade head` to a temporary database and asserts Alembic's own `compare_metadata` finds no difference against `models.Base.metadata`, which is what catches a model field added without a migration.

Batch tests (`tests/unit/test_batch.py`) use no browser and no network: a fake strategy is registered over an entry in `STRATEGIES` with `monkeypatch.setitem`, and the real scheduler, worker, and repositories run against a real SQLite file. The fake records its own per-class high-water mark, so a concurrency ceiling is *observed* rather than assumed. It also gates on an explicit target — each extraction blocks until the expected number are simultaneously live — because simply sleeping made the assertion a race against however long the workers spent in the database, and it under-counted on a fast machine.

`tests/unit/test_cli_scan.py` covers the subcommand the same way, driving the real command against a temporary database with a fake strategy. Every argument namespace in it is produced by `cli.main.build_parser` rather than hand-built, so an unregistered subcommand or a renamed flag fails there instead of in a manual run.

The repo does not use `pytest-asyncio`. Async tests are driven on a background thread with a fresh event loop by `tests/unit/asyncio_harness.py`, because the snapshot suite drives Chromium through Playwright's sync API and leaves a running-loop registration on the main thread's `asyncio.events` state that makes both `asyncio.run` and a main-thread `run_until_complete` raise for the rest of the session. `tests/unit/test_prefiltered.py` carries the original diagnosis. The harness also owns engine disposal: an async engine outliving its loop leaves aiosqlite holding a closed one, and the error surfaces in whichever test runs next, so `run_async` disposes every engine passed to `track_engine` on the loop that created it.

Captures are produced by `scripts/capture_snapshot.py -c <handle> --expected N` (pass `--paginate` if and only if the entry has `paginate=True`).

## Adding a company

The canonical workflow is the `integrate-company` skill, driven by the tracked `new-companies.json` queue whose schema is documented by `new-companies.example.json`. First step is always `scripts/probe_board.py` — read-only reconnaissance that produces an anchor census, frame/shadow map, pagination affordances, platform fingerprint, and a suggested `Company(...)` snippet. Then follow the fixed pipeline: unfiltered ground-truth measurement, live integration, exact `Output:` report capture, machine validation, snapshot capture, quality gates, and one `feat:` commit covering `catalog/companies.py`, the new snapshot directory, and the queue-entry removal when that tracked workflow change is intended.

The `expected_jobs` value is immutable onboarding-only queue evidence. Validate it as a nonnegative JSON integer, keep it in working context, and never copy it into `Company`, runtime reports, or the database. Failure at any stage is reported as a blocker with evidence in `blockers/INTEGRATION_BLOCKERS.md` rather than worked around by loosening the queue evidence.

## Development Conventions

Python 3.12+, uv-managed. Ruff enforces line-length 88, double quotes, and rules `E,F,I,W,UP,B,C4,SIM`. Mypy strict (`disallow_untyped_defs`) means every function is fully annotated. Import package modules as `from job_discovery.X`; the package lives under `src/`.

Two files are E501-exempt on purpose: `extraction/dom/agent/prompt.py` and `tests/unit/test_prompt_render.py`. Prompt clauses and their golden copies are byte-exact single-line literals — never reflow them.

Changes reach `main` only through a pull request: commit on a branch, push it, and open one. The ruleset on `main` blocks direct pushes and force-pushes and allows only a squash merge, once `ci-ok` and `pr-title` pass and the branch is up to date with `main`. Never bypass the pre-commit hooks with `--no-verify`; if an auto-fixer modifies a file, re-stage and commit again. Commit messages are a single short sentence, 100 characters max, subject line only, no body. Reasoning belongs in the code, the design docs, or `blockers/`, not in the commit message. The squash merge makes the PR title the commit subject on `main`, with no body, so the PR title follows the same rule — `pr-title` enforces a conventional-commit type and a length that still fits in 100 after GitHub appends ` (#N)`.

Prefer clean abstractions in the design over per-site code branches. Every opt-in knob defaults to inert so adding one cannot change any existing board's behaviour. Per-board differences live in configuration, not in code — no company name, host, or posting id ever appears in the matcher.

## Tabnine-specific guidance

### Remote codebase research routing

When a task involves researching, investigating, exploring, or searching code in remote repositories (i.e., **not** the local workspace), you MUST delegate the task to the `remote-codebase-investigator` subagent. Do NOT call `tabnine-context__*` MCP tools directly — always route through the subagent instead. This keeps the main-session context lean and prevents remote exploration from polluting the local investigation record.

### Skills

The `integrate-company` skill lives at `.tabnine/agent/skills/integrate-company/SKILL.md`. Activate it when the user names a company they want integrated into the corpus, when debugging why an already-configured company returns the wrong job count, or when troubleshooting zero/short/over-count extraction at integration time. The full entry (name, aliases, job_board_url, sample_job_url, expected_jobs) is read from `new-companies.json` at the repo root. Only the first four fields inform `Company`; `expected_jobs` remains external onboarding evidence.

The skill is maintained in two copies, `.claude/skills/integrate-company/` and `.tabnine/agent/skills/integrate-company/`. They are identical except for two deliberate per-assistant adaptations: the Playwright MCP tool prefix (`mcp_playwright_` here, `mcp__playwright__` for Claude Code) and the guidance file each cites for the commit convention. Keep every other change in sync across both copies; `tests/unit/test_skill_sync.py` fails on any other difference, and a new deliberate one goes into its `SKILL_ADAPTATIONS` table.

### Playwright MCP

The `.mcp.json` at the repo root registers `@playwright/mcp` with the `devtools` capability. This is the reconnaissance surface for exploring a live career board when the deterministic `scripts/probe_board.py` output is not enough. Prefer the probe script for anything reproducible; use the Playwright MCP for one-shot investigative work.
