# Vacantes — Transition Plan

From `job-agent-lab`, a research sandbox with one sequential CLI, to `vacantes`, a
product with three components — **extraction**, **persistence**, **batch** — over one
shared core, answering one question a few times a week: *what job postings are open
right now.*

Status: **Phase 0 landed** (2026-09-15) in four commits, except T0.9 — the GitHub
repository rename — which is deferred by choice. **Phase 1 landed** (2026-09-15).
**Phase 2 landed** (2026-09-15). **Phase 3 landed** (2026-09-15). **Phase 4
landed** (2026-09-15). All five phases are now delivered; T0.9 — the repository
rename — is the only outstanding item, and this file stays until it lands, then
is removed whole.
Supersedes `ORCHESTRATION.md`, whose technical decisions are carried forward here
where they still hold and corrected where they do not.

Lifecycle: this file is transient. Each phase, once landed, graduates its durable
rationale into `ARCHITECTURE.md` (design) and `TABNINE.md` (per-feature notes). Its
section here is **kept**, marked landed, with the as-built deviations recorded above
the original text, so the plan doubles as the audit trail of what changed between
design and delivery — that record has already caught two doc/test drifts. When the
last phase lands, this file is removed whole.

## 0. Decisions

| Decision | Value | Why |
|---|---|---|
| Project / package / repo name | `vacantes` | The noun for what the database holds; free on PyPI and in the `ticolab` org; tied to the org's identity without hardcoding a region in code (`TargetRegion` already parametrizes that) |
| Component 1 | `extraction/` (existing name kept) | Names the responsibility. Only 1 of 7 registered strategies drives an LLM agent; the agent is a detail inside `extraction/dom/` |
| Component 2 | `persistence/` | Precise and technology-neutral, which matters because SQLite → PostgreSQL is planned as a connection-string change |
| Component 3 | `batch/` | §16 of the old proposal says this is "a semaphore and a gather" and rejects orchestration frameworks; a package named `orchestration` invites exactly that thinking. `batch` matches the CLI mode and the `batch_id` column |
| Shared kernel | `domain/`, `catalog/`, `settings.py` | The vocabulary all three components speak; the layering rule in §2.3 rests on it |
| Agent wiring | `extraction/dom/agent/` (folded from top-level `navigation/`) | `navigation/` has exactly one consumer, `extraction/dom/strategy.py`; the tree should say so |
| CLI | One console script `vacantes` with subcommands `integrate` and `batch`; `job-agent-lab` kept as a frozen alias | Removes the `job-agent-lab-batch` naming that was shaped around the old name, while keeping the integration workflow byte-identical |
| Concurrency buckets | `browser` / `http`, classified by **cost**, not by strategy name | `coveo` with `browser_token_key` launches Chromium; it is not "effectively free" (§6.1) |
| Database | SQLite via SQLAlchemy 2.x + `aiosqlite`, Alembic migrations, `data/vacantes.db` (gitignored) | Carried from the old proposal; sized correctly for ~250 companies × tens of URLs, one writer, one machine |

## 1. Why

### 1.1 Two purposes, one core

The `job-agent-lab` CLI resolves zero-or-one company handle, runs extraction
sequentially, and writes one timestamped JSON file per company to `./output/`. That is
the right shape for its purpose: the `integrate-company` skill drives it to onboard and
debug one board at a time, and the JSON artifact is the evidence a human reviews before
committing a catalog entry.

It is the wrong shape for the second purpose. The corpus is growing toward 250 companies
and will run two or three times a week. Sequential execution over 250 boards, many of
which drive a real browser and an LLM agent, is measured in hours; a mid-run crash
discards all completed work; and a directory of timestamped JSON files is an archive,
not a queryable dataset.

Both purposes call the same unit of work, which already exists and is already the
correct seam:

```python
strategy = get_strategy(company.strategy)
report = await strategy.extract(company, ctx)
```

`ExtractionStrategy.extract` is async, takes a frozen `RunContext`, returns a report
dict authored solely by `build_report`, and knows nothing about its caller. The
integration CLI is one caller that iterates sequentially; the batch scheduler is a
second caller that schedules the same coroutine concurrently. Nothing under
`extraction/` changes to support the second caller.

### 1.2 Why the name and the tree change too

"Lab" names a *mode* (research) and "agent" names a *mechanism* (the LLM). Neither
describes the core. Of the seven registered strategies — `dom`, `greenhouse`, `phenom`,
`talentbrew`, `coveo`, `peopleforce`, `bamboohr` — only `dom` drives an agent; the rest
are plain HTTP. `CLAUDE.md`'s own north star is that extraction is deterministic and the
agent is a navigation aid. And once the persistence and batch packages land, the shared
core is the majority of the package while the lab CLI is 178 lines of it.

The word has already leaked into the shared kernel's docstrings — `domain/__init__.py`
("Pure-domain types for the job-agent lab"), `extraction/__init__.py`,
`catalog/__init__.py`, and `Company` itself ("A career site the agent lab tests
against"). Those are the modules a scheduled batch will import while writing to a
database. The docstrings become false the day Phase 2 lands.

Renaming *before* Phase 1 means the eleven new modules are born under the right name
instead of being swept later, and the design (this document) is written once against
the final layout.

## 2. Target shape

### 2.1 Tree

```
src/vacantes/
├── __init__.py
├── settings.py              shared defaults (model, steps, render-settle, UA)
├── domain/                  shared kernel: Company, LinkRule, RuntimeHooks,
│                            TargetRegion, ExtractionResult
├── catalog/                 the corpus (COMPANIES, find_company, slugify);
│                            source of truth — persistence projects it, never owns it
├── extraction/              COMPONENT 1 — the port and every strategy
│   ├── base.py                ExtractionStrategy, RunContext, STRATEGIES, build_report
│   ├── dom/                   browser strategy
│   │   ├── strategy.py, collector.py, rules.py, assets/*.js
│   │   └── agent/             ← navigation/ folded in: prompt.py, runner.py, controller.py
│   └── ats/                   HTTP adapters (greenhouse, phenom, talentbrew, coveo,
│                              peopleforce, bamboohr) + browser_token.py
├── persistence/             COMPONENT 2
│   ├── engine.py              async engine, session factory, SQLite pragmas
│   ├── models.py              SQLAlchemy 2.x declarative models
│   ├── catalog_repo.py        projects catalog.COMPANIES into `companies`
│   ├── jobs_repo.py           atomic replace of a company's current URL set
│   └── runs_repo.py           run lifecycle, freshness, stale-run reaping
├── batch/                   COMPONENT 3
│   ├── policy.py              concurrency class, skip/retry decisions
│   ├── worker.py              the per-company unit of work
│   └── scheduler.py           semaphores, fan-out, catalog sync, reaping
├── cli/
│   ├── main.py                `vacantes` dispatcher: integrate | batch
│   ├── integrate.py           today's cli.py, moved; args unchanged
│   └── batch.py               argparse for the batch subcommand
└── reporting/               JSON + terminal summary (integrate always; batch optional)

migrations/                  Alembic (env.py imports vacantes.persistence.models)
data/                        gitignored; default database at data/vacantes.db
```

### 2.2 Console scripts

```toml
[project.scripts]
vacantes      = "vacantes.cli.main:main"       # subcommands: integrate, batch
job-agent-lab = "vacantes.cli.integrate:main"  # frozen alias — see §3.1
```

The alias points straight at the integrate parser's own `main()`, not through the
dispatcher, so its argument surface is identical to today's by construction.
`job-agent-lab-batch` never ships.

### 2.3 Layering — the rule, and the test that makes it real

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

Both `persistence` and `batch` landed narrower than this section first anticipated,
for one reason: a component that is *handed* what it needs — the companies to run,
the session factory to use — does not import it. `cli` correspondingly landed one
edge *wider*: it reaches `persistence` as well as `batch`, because the command is
the composition root that opens the engine and hands the session factory inward.
The extra edge there and the missing one below it are the same fact.
`CLAUDE.md`, `ARCHITECTURE.md`, and `TABNINE.md` carry the full explanation beside
the same table.

This is the property that makes the shared-code constraint (§3.2) structural rather
than a matter of discipline: a strategy cannot write to the database, cannot know a
batch is running, and cannot behave differently under the scheduler than under the
integration CLI.

The old proposal stated the rule; nothing enforced it. Phase 0 added
`tests/unit/test_layering.py`, which parses the imports of every module under
`src/vacantes/` with `ast` and asserts each package imports only from its allowed set.
The current graph is already clean — verified 2026-09-15: `extraction/base.py` imports
only `domain`; `domain/` imports no sibling; every apparent cross-layer reference
(`domain/region.py` → navigation, `extraction/base.py` → reporting,
`ats/phenom.py` → catalog) is a docstring cross-reference, not an import. The
allowlist is therefore written from the actual graph and may only ever shrink.

## 3. Constraints

### 3.1 The integration command is frozen

`job-agent-lab -c <handle>` keeps its argument surface, defaults, output path
(`./output/<slug>_<timestamp>.json`), report shape (`build_report` is untouched), and
the summary lines the `integrate-company` skill reads (`Jobs:`, `Verdict:`, `Done:`,
`Errors:`). The module may move (`cli.py` → `cli/integrate.py`); the behaviour may not.

Two visible strings change and are called out as the only exceptions: the banner
`Agent Lab - Extracting jobs from N company(ies)` and the `--help` description
`Browser-use agent lab for job extraction`. Verified 2026-09-15 that nothing parses
either — the one match for "Agent Lab" outside `src/` is the skill's title heading.

### 3.2 Batch executes the same extraction code

Both entry points call the same `extract` coroutine with the same `RunContext` shape.
Divergence between "what the agent does during onboarding" and "what the scheduled run
does" would make the integration workflow meaningless as a correctness signal. §2.3 is
how this is enforced.

### 3.3 Deployment and scope

One local machine, one operator, one writer. No API, no UI. Only the *current* set of
job URLs matters; history is a non-goal (§10).

## 4. Phase 0 — Rename and restructure — **LANDED** (T0.9 deferred)

No behaviour change. Everything here is a move, a rename, or a docstring. It is the
prerequisite for every later phase and should land before Phase 1 starts.

### 4.1 Inventory (measured 2026-09-15)

| What | Count | Where |
|---|---|---|
| Occurrences of `job_agent_lab` / `job-agent-lab` | 360 across 67 files | 57 `.py`, 6 `.md`, 1 yaml, 1 toml, 1 Makefile, 1 lock |
| Import lines | 150 | `src/`, `scripts/`, `tests/`, both skill copies |
| String-literal package paths a `sed` on imports would miss | 2 + 8 | `files("job_agent_lab.extraction.dom")` ×2 in `extraction/dom/__init__.py` (fails at import — loud); `monkeypatch.setattr("job_agent_lab...")` ×7 in `tests/unit/test_catalog.py` and ×1 in `tests/unit/test_prefiltered.py:401` (fail at test time — loud) |
| `pyproject.toml` | 5 | `name`, `[project.scripts]`, `packages = ["src/job_agent_lab"]`, `include = ["src/job_agent_lab/extraction/dom/assets/*.js"]`, per-file-ignore `"src/job_agent_lab/navigation/prompt.py"` |
| `.pre-commit-config.yaml:34` | 1 | `files: '^(src/job_agent_lab/(extraction\|navigation\|domain)/.*\|tests/.*)$'` — the trigger for the full-suite hook |
| `Makefile:1` | 1 | comment |
| `uv.lock` | 1 | regenerates via `uv sync` |
| "lab" in `src/` prose | 8 | `cli.py:1,65,112`, `settings.py:1`, `catalog/__init__.py:4`, `extraction/__init__.py:1`, `domain/__init__.py:1`, `domain/company.py:480` |
| Docs | 4 files | `CLAUDE.md` (8 token / 5 prose), `TABNINE.md` (9 / 7), `README.md` (5 / 5), `ARCHITECTURE.md` (1 / 1) |
| Skills | 2 copies, 18 refs each | `.claude/skills/integrate-company/` and `.tabnine/agent/skills/integrate-company/`. Planned as "already drifted"; **that was wrong** — the only two differences are the Playwright MCP tool prefix and which guidance file each cites, both deliberate per-assistant adaptations. Kept, and documented in `CLAUDE.md` and `TABNINE.md` so they are not re-flagged as drift |
| Stale doc paths (separate commit) | 20 files | reference `blockers/INTEGRATION_BLOCKERS_R2.md` or `spike/INTEGRATION_BLOCKERS.md`, neither of which exists |

Nothing in this inventory fails silently. Every string-literal path is load-bearing
enough to fail at import or test time, and the 843-test suite, mypy strict, and the
pre-commit gate stand behind the mechanical edits.

### 4.2 Steps

**T0.1 Baseline.** Before touching anything, record reference runs for later
byte-comparison: `uv run job-agent-lab -c speechify` (API strategy — deterministic, no
LLM) and `uv run job-agent-lab -c commandlink` (DOM strategy on the agent-less
`pre_filter_urls` path — deterministic, exercises `extraction/dom` imports). Keep the
JSON reports and summaries.

**T0.2 Package rename.** `git mv src/job_agent_lab src/vacantes`; rewrite the 150
import lines and the 10 string-literal paths; update the five `pyproject.toml` sites,
the pre-commit `files` regex (see T0.3 for its final form), the Makefile comment; `uv
sync` to regenerate the lock. Register `job-agent-lab = "vacantes.cli:main"` for now so
the alias never stops resolving between T0.2 and T0.4.

**T0.3 Fold the agent wiring.** `git mv src/vacantes/navigation
src/vacantes/extraction/dom/agent`. Update `extraction/dom/strategy.py` imports, the
`test_prefiltered.py:401` monkeypatch target, `tests/unit/test_prompt_render.py`'s
import, the per-file-ignore path (now
`"src/vacantes/extraction/dom/agent/prompt.py"`), and the `_extract_prefiltered`
docstring that says it "never imports `navigation.runner`". The pre-commit regex
becomes `^(src/vacantes/(extraction|domain)/.*|tests/.*)$` — `navigation` is now under
`extraction` and covered.

**T0.4 CLI package.** `git mv src/vacantes/cli.py src/vacantes/cli/integrate.py`; add
`cli/__init__.py` and `cli/main.py` (argparse subparsers; `integrate` reuses the same
argument-adding function `integrate.py` uses for its own parser, so the two surfaces
cannot drift). Console scripts per §2.2. `cli/batch.py` is a stub until Phase 3.

**T0.5 Docstring and help sweep.** The eight `src/` sites in §4.1, plus the banner and
`--help` description (§3.1). "the lab" → the product; `Company` becomes "a career site
in the corpus".

**T0.6 Docs.** `CLAUDE.md`, `TABNINE.md`, `README.md`, `ARCHITECTURE.md`: name,
layout, commands, the three components and the kernel, the layering rule, the alias.
Reconcile the two skill copies (they have diverged) and update both to the new import
path in their `extractor_ground_truth.py` while keeping `uv run job-agent-lab` as the
documented command.

**T0.7 Layering test.** `tests/unit/test_layering.py` per §2.3.

**T0.8 Stale paths.** Fix the 20 references to non-existent blocker docs. Own commit.
Scoped to *blocker* documents only: 10 citations of the retired
`ARCHITECTURE_PROPOSAL_R2.md` remain, and they name section numbers (§4.5, §4.7,
§4.8.1–3) whose content is not in `ARCHITECTURE.md`. That file was never committed —
verified with `git log --all --diff-filter=A` — so the text is unrecoverable and
repointing would mean inventing a target. Tracked as an open item in §11.

**T0.9 Repository rename.** `ticolab/job-agent-lab` → `ticolab/vacantes` on GitHub;
`git remote set-url origin git@github-ticolab:ticolab/vacantes.git`. GitHub redirects
the old URL. Independent of every other step and fully reversible.

### 4.3 Acceptance

- `uv run --group test pytest tests/` — **observed: 850 pass**, the 843 baseline plus
  7 layering tests.
- `uv run ruff format . && uv run ruff check . && uv run mypy src scripts tests` clean.
  Note the pre-commit mypy hook type-checks only the *staged* files and is stricter than
  the repo-wide run (bare `return await <imported coroutine>` trips `no-any-return`
  there); a green repo-wide run is not a guarantee the commit passes.
- The pre-commit full-suite hook still fires for a change under
  `src/vacantes/extraction/` — **observed** on the three commits touching it.
- `uv run job-agent-lab -c speechify` and `-c commandlink` produce reports identical to
  T0.1's baselines modulo `extraction_time_seconds`; `uv run vacantes integrate -c
  speechify` produces the same; `uv run vacantes --help` lists both subcommands.
  **Observed**, and additionally against a Speechify report captured before the rename
  existed — an independent baseline. `vacantes batch` exits 2 with a pointer rather
  than silently no-opping.
- The `integrate-company` skill runs end to end on one queued company without edits
  beyond T0.6.

### 4.4 Commits

1. `refactor: rename package to vacantes, fold agent wiring under extraction/dom, add
   cli package` — T0.2–T0.4 together (all pure moves; splitting them means two passes
   over the same 150 import lines). Tests must be green at this commit.
2. `docs: rename the lab to vacantes across docstrings, help text, docs, and skills` —
   T0.5, T0.6.
3. `test: enforce package layering with an import-graph test` — T0.7.
4. `docs: fix stale blocker-document paths` — T0.8.
5. T0.9 is outside the repository.

### 4.5 Rollback

Before commit 1 lands: `git checkout -- . && git clean -fd`. After: revert the commit;
`git mv` history survives rename detection, so blame and `git log -S` remain usable
across the boundary.

## 5. Phase 1 — Persistence (`vacantes/persistence/`) — **LANDED**

Carried from the old proposal with names updated. Nothing calls this package until
Phase 2; the integration CLI acquires no new import.

**As built, four deviations from the text below.** `slugify` moved from
`catalog/__init__.py` into `domain/company.py`, which also gained a derived
`Company.slug` property; `catalog` re-exports the function so every existing caller is
unchanged. This was forced by §2.3: `sync_catalog` takes `Sequence[Company]` and needs
a slug, but the layering rule gives `persistence` no route to `catalog`. A new
`persistence/timestamps.py` makes naive UTC a normalized invariant rather than a
convention — SQLite stores a `DateTime` as an ISO-8601 string and compares
lexicographically, so an aware value's `+00:00` suffix would mis-order against a naive
one and misjudge §6.4's freshness predicate with nothing failing loudly. `jobs_repo`
gained a `list_company_urls` read helper so no caller builds a query outside the
package. And `reap_stale_runs` selects its target ids and returns their count instead
of reading `Result.rowcount`, which is absent from the typed protocol and would have
required a cast.

**Three latent bugs surfaced during verification**, each fixed rather than shipped. The
pragma listener guarded on `isinstance(conn, sqlite3.Connection)`, but SQLAlchemy passes
its `AsyncAdapt` wrapper around an `aiosqlite.Connection`, so the guard failed and
*every pragma was silently skipped* — WAL off, foreign keys unenforced. Alembic's stock
`env.py` calls `fileConfig` with its default `disable_existing_loggers=True`, which
disables every `vacantes` logger the moment a migration runs in-process; two unrelated
`caplog` tests caught it. And the test harness leaked undisposed async engines, leaving
aiosqlite holding a closed event loop and surfacing the error in whichever test ran
next.

### 5.1 Technology

SQLite through SQLAlchemy 2.x with `aiosqlite`; Alembic owns the schema. SQLite is sized
correctly: ~250 companies × tens of URLs is low tens of thousands of rows, written a few
times a week by one process. A database server would add an operational dependency that
buys nothing; backup is a file copy. SQLAlchemy is used despite the modest schema so the
eventual PostgreSQL move is a connection-string change, and Alembic supplies schema
discipline instead of hand-edited DDL. The async driver keeps database calls off the
event loop that is concurrently driving browser sessions.

### 5.2 Engine configuration

Applied once at engine creation in `engine.py`:

```sql
PRAGMA journal_mode = WAL;      -- readers never block the writer
PRAGMA busy_timeout = 5000;     -- wait on the write lock instead of erroring
PRAGMA synchronous  = NORMAL;   -- durable enough for a regenerable dataset
PRAGMA foreign_keys = ON;       -- SQLite disables FK enforcement by default
```

Without `busy_timeout`, concurrent workers produce intermittent `database is locked`
errors. Without `foreign_keys`, the `REFERENCES` clauses below are inert documentation.

### 5.3 Data model

Three tables. `catalog/companies.py` remains the source of truth; `companies` is a
projection refreshed at the start of every batch so the database is self-describing.

```sql
CREATE TABLE companies (
    slug            TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    job_board_url   TEXT NOT NULL,
    strategy        TEXT NOT NULL,
    expected_jobs   INTEGER,
    synced_at       TIMESTAMP NOT NULL
);

CREATE TABLE company_runs (
    id              INTEGER PRIMARY KEY,
    company_slug    TEXT NOT NULL REFERENCES companies(slug) ON DELETE CASCADE,
    batch_id        TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('in_progress','success','failed')),
    started_at      TIMESTAMP NOT NULL,
    finished_at     TIMESTAMP,
    url_count       INTEGER,
    verdict         TEXT,
    error_message   TEXT
);

CREATE TABLE job_urls (
    id              INTEGER PRIMARY KEY,
    company_slug    TEXT NOT NULL REFERENCES companies(slug) ON DELETE CASCADE,
    url             TEXT NOT NULL,
    captured_at     TIMESTAMP NOT NULL,
    UNIQUE (company_slug, url)
);

CREATE INDEX ix_job_urls_company     ON job_urls (company_slug);
CREATE INDEX ix_company_runs_lookup  ON company_runs (company_slug, status, finished_at);
```

Keeping `job_urls` separate from `company_runs` is load-bearing: `job_urls` answers
"what is open", `company_runs` answers "did we successfully look, and when". An
`updated_at` column on one table would conflate a failed run with a successful one and
break the retry semantics in §6.4. `verdict` stores what `compute_verdict` already
produces, so drift from a human-counted `expected_jobs` is a direct query.

### 5.4 Repository interface

Every SQL statement lives in one of three repository modules; no other module imports a
session, builds a query, or opens a transaction. Transaction boundaries, retries, and
error translation get exactly one home, and the data layer is testable without a
scheduler.

```python
# persistence/jobs_repo.py
async def replace_company_urls(
    session: AsyncSession, *, company_slug: str, urls: Sequence[str], captured_at: datetime
) -> int: ...

# persistence/runs_repo.py
async def start_run(session: AsyncSession, *, company_slug: str, batch_id: str) -> int: ...
async def finish_run(session: AsyncSession, *, run_id: int, url_count: int, verdict: str) -> None: ...
async def fail_run(session: AsyncSession, *, run_id: int, error: str) -> None: ...
async def has_fresh_success(session: AsyncSession, *, company_slug: str, since: datetime) -> bool: ...
async def reap_stale_runs(session: AsyncSession, *, older_than: datetime) -> int: ...

# persistence/catalog_repo.py
async def sync_catalog(session: AsyncSession, companies: Sequence[Company]) -> None: ...
```

### 5.5 The atomic replace

No history is kept, so a successful extraction replaces the company's URL set
wholesale. Delete-and-insert in one transaction is simpler than upsert-plus-prune and
cannot leave stale URLs behind.

```python
async def replace_company_urls(session, *, company_slug, urls, captured_at):
    async with session.begin():
        await session.execute(delete(JobUrl).where(JobUrl.company_slug == company_slug))
        session.add_all([
            JobUrl(company_slug=company_slug, url=u, captured_at=captured_at)
            for u in dict.fromkeys(urls)          # de-duplicate, preserve order
        ])
    return len(set(urls))
```

The transaction is the failure boundary: a crash before it leaves the previous snapshot
intact; a crash inside it rolls back to that same snapshot; a reader never sees a
partially replaced set. `dict.fromkeys` collapses duplicates a board may emit while
keeping the strategy's order, satisfying `UNIQUE (company_slug, url)` without
`ON CONFLICT`.

### 5.6 Direct writes, no queue

Workers call repositories directly. A queue-plus-single-writer earns its complexity when
write throughput saturates the database, writes need batching, or several processes
need coordinated ordering; none holds here. Each company produces one transaction of a
few dozen rows after minutes of extraction, so the database is idle almost always, and
SQLite serialises writes on a database-level lock regardless. If contention ever
appears, a queue slots in behind the unchanged repository interface.

### 5.7 Tasks and acceptance

- **T1.1** `engine.py` with pragmas and session factory; `models.py`; `data/` gitignored.
- **T1.2** Alembic: `migrations/` at repo root, `env.py` importing
  `vacantes.persistence.models`, initial revision generating §5.3.
- **T1.3** The three repositories per §5.4.
- **T1.4** Tests against a **real SQLite file** under `tmp_path` — not `:memory:`,
  which exercises neither WAL nor `busy_timeout`, the two settings that matter under
  concurrency. Coverage: atomic replace leaves no stale rows; a rolled-back transaction
  preserves the previous snapshot; freshness ignores failed runs; the reaper marks only
  genuinely stale `in_progress` rows.
- **T1.5** Dependencies: `sqlalchemy>=2.0`, `aiosqlite>=0.20`, `alembic>=1.13`.
- Acceptance: unit tests green; layering test proves `persistence` imports only
  `domain`/`settings`; `job-agent-lab` imports nothing new (assert via the layering
  test's edge for `cli.integrate`).

## 6. Phase 2 — Batch core (`vacantes/batch/`) — **LANDED**

**As built, four deviations from the text below.** The per-company timeout lives on
`BatchPolicy`, not on the run context: §6.5's `ctx_timeout_seconds` has no counterpart on
the real `RunContext`, which is the frozen four-field port *both* entry points pass
through, and widening it with a batch-only concern is exactly the divergence §3.2
forbids. The layering row is narrower than §2.3 anticipated — `batch → domain,
extraction, persistence` — because the scheduler is handed its companies and its session
factory, and the ceilings are policy that lives in `batch/policy.py` rather than in
`settings.py`. Every repository call takes **its own short-lived session**: each Phase 1
repository opens its own transaction, so a session that has already autobegun one for a
read cannot begin another, and sharing one raises `InvalidRequestError`. And
`company_runs.url_count` stores the de-duplicated count returned by
`replace_company_urls` rather than `len(jobs)`, so the recorded number is one a
`select count(*) from job_urls` can actually reproduce.

The reaper's cutoff is `now - policy.timeout`: no run may legitimately outlive the
ceiling that bounds it, so anything older is abandoned by definition. That inference
holds only under §3.3's one-writer deployment — two concurrent batches would let one
reap the other's live runs.

### 6.1 Concurrency classes — corrected

Execution is `asyncio`, matching the codebase: `browser-use` and Playwright are
async-native and `extract` is a coroutine. Two semaphores rather than one, because the
two cost profiles differ by an order of magnitude: a browser run holds a Chromium context
(memory) and an LLM agent (provider rate limits); an HTTP run is a JSON call bounded
only by politeness.

The old proposal split the buckets by strategy *name* (`dom` vs everything else). That
is wrong in one case: `coveo` with `browser_token_key` set (UST, the C19 closure) opens a
`BrowserSession` to read the token out of page state — `extraction/ats/browser_token.py`
is the only real `browser_use` import outside `extraction/dom/`. Classification is by
**cost**, and it takes the `Company`, not the strategy name:

```python
def concurrency_class(company: Company) -> Literal["browser", "http"]:
    if company.strategy == "dom":
        return "browser"
    if (
        company.strategy == "coveo"
        and company.coveo is not None
        and company.coveo.browser_token_key is not None
    ):
        return "browser"
    return "http"
```

(`Company.coveo` is `CoveoConfig | None`; the schema validator already ties
`browser_token_key` to `strategy="coveo"`, so the guard is for the type checker, not
for a reachable state.)

Defaults: 4 concurrent browser runs, 20 concurrent HTTP runs, both tunable. A unit test
asserts every key in `STRATEGIES` is classified (mirroring the existing test that pins
`StrategyName` to the registry) **and** that the coveo browser-token variant lands in
`browser`, so a future adapter that grows a browser dependency fails loudly rather than
silently exhausting memory in the 20-wide bucket.

### 6.2 Fan-out

One task list, one `gather`, each task selecting its own semaphore:

```python
async def run_batch(companies, ctx, limits):
    sems = {"browser": asyncio.Semaphore(limits.browser),
            "http": asyncio.Semaphore(limits.http)}

    async def _guarded(company: Company) -> CompanyOutcome:
        async with sems[concurrency_class(company)]:
            return await run_company(company, ctx)

    return await asyncio.gather(*(_guarded(c) for c in companies))
```

`return_exceptions=True` is deliberately not used; §6.5 catches inside the worker so
every task resolves to an outcome value and aggregation needs no `isinstance` checks.
`concurrency_class` lives in `batch/policy.py`, not on the `ExtractionStrategy`
protocol — widening the protocol would make every existing strategy non-conforming.

### 6.3 Run lifecycle

```
                 ┌──────────────┐
  skipped ◄──────┤   selected   ├──────► in_progress ──► success
  (fresh run)    └──────────────┘                   └──► failed
```

A row enters `company_runs` as `in_progress` when the worker starts and is updated to
`success` or `failed` exactly once. Skipped companies produce no row: the history
records attempts, not considerations.

### 6.4 Skip policy and crash recovery

Before extracting, the worker asks whether the company has a *successful* run inside the
freshness window (default 20 hours):

```sql
SELECT 1 FROM company_runs
 WHERE company_slug = :slug AND status = 'success' AND finished_at >= :cutoff
 LIMIT 1;
```

`status = 'success'` is the point. An `updated_at` touched by failures as well would
skip a board that failed this morning as "done" this afternoon, and it would silently
stop being retried. Keying on successes means failures are always eligible for retry
and successes are never repeated within the window. `--force` bypasses the check.

A batch killed mid-run leaves `in_progress` rows; the next batch reaps them at startup:

```sql
UPDATE company_runs
   SET status = 'failed', error_message = 'abandoned: batch terminated', finished_at = :now
 WHERE status = 'in_progress' AND started_at < :cutoff;
```

Reaped companies fail the freshness check and re-run; companies that completed before
the crash are skipped. Resumption falls out of the freshness rule — no checkpoint file.

### 6.5 Failure isolation

```python
async def run_company(company, ctx) -> CompanyOutcome:
    run_id = await runs_repo.start_run(...)
    try:
        report = await asyncio.wait_for(
            get_strategy(company.strategy).extract(company, ctx),
            timeout=ctx_timeout_seconds,
        )
    except TimeoutError as exc:
        await runs_repo.fail_run(run_id=run_id, error=f"timeout after {ctx_timeout_seconds}s")
        return CompanyOutcome.failed(company, exc)
    except Exception as exc:                     # any third-party board failure
        await runs_repo.fail_run(run_id=run_id, error=repr(exc))
        return CompanyOutcome.failed(company, exc)
    await jobs_repo.replace_company_urls(...)
    await runs_repo.finish_run(run_id=run_id, ...)
    return CompanyOutcome.succeeded(company, report)
```

A broad `except Exception` is correct at exactly this boundary: failure modes across 250
third-party sites are open-ended (Playwright crashes, rate limits, DNS, redesigns), and
the contract is that no single board aborts the batch. `KeyboardInterrupt` and
`SystemExit` derive from `BaseException` and still propagate. An extraction returning
zero jobs is a **successful run with an unhappy verdict**, not a failure — only an
exception marks `failed`, so retries target genuine breakage, not empty boards.

### 6.6 Tasks and acceptance

- **T2.1** `policy.py`: `concurrency_class`, freshness/skip decision, limits dataclass.
- **T2.2** `worker.py`: `run_company`, `CompanyOutcome`.
- **T2.3** `scheduler.py`: `run_batch`, catalog sync, stale-run reaping, aggregation.
- **T2.4** Tests with a fake strategy registered in `STRATEGIES` — no browser, no
  network. Coverage: concurrency never exceeds the per-class ceiling (fake records its
  own high-water mark); one company raising leaves the rest unaffected; a company over
  its timeout is recorded `failed`; a fresh company is skipped while a previously failed
  one is retried; a seeded stale `in_progress` row is reaped and re-run; the coveo
  browser-token variant classifies as `browser`.
- Acceptance: unit tests green; layering test proves `extraction` still imports neither
  `persistence` nor `batch`.

## 7. Phase 3 — The `vacantes batch` subcommand — **LANDED**

**As built, four deviations from the text below.** The flag list grew by three.
`-m/--model` and `--max-steps` are not optional extras: `RunContext` requires both,
and §7's list simply omitted them. `--database PATH` was added so a run can be
pointed at a file other than `settings.DATABASE_PATH`, which is what let the kill
and resume behaviour below be validated without touching the real dataset. The
command also **preflights the schema** before doing anything, because connecting to
a SQLite path that does not exist creates an empty file quite happily; without the
check the first ever batch on a fresh checkout would die with an `OperationalError`
from inside a repository rather than naming the `alembic upgrade head` the operator
owes. `persistence/engine.missing_tables` answers that question, since nothing
outside `persistence/` may build a query. The layering row is the one in §2.3, a
row wider than anticipated for the reason recorded there. And while per-company
JSON does route through `reporting.output.save_result` exactly as specified — the
artifact is byte-shape-identical to `integrate`'s, same filename convention
included — the **aggregate summary is rendered by the command**, because it reads
the batch's own result object and teaching `reporting/` to understand the `batch`
package would invert a dependency to buy a symmetry nothing needs.

The exit code reports **whether the batch ran, not whether every board succeeded**.
A board-level failure is already data, recorded in `company_runs` with its error
text; a non-zero exit is reserved for the batch being unable to start at all — an
unresolvable handle, an empty selection, a database with no schema. This matches
`integrate`'s default, so neither entry point teaches a different habit.

```
vacantes batch [--all | --companies FILE | -c HANDLE ...] [--exclude HANDLE ...]
               [--browser-concurrency N]   # default 4
               [--http-concurrency N]      # default 20
               [--freshness-hours N]       # default 20
               [--force]                   # ignore freshness, run everything
               [--timeout-seconds N]       # per-company ceiling
               [--dry-run]                 # print the plan, touch nothing
               [--json-output DIR]         # additionally write per-company JSON
```

Handle resolution reuses `find_company`, so alias / acronym / substring rules match
`integrate` exactly; unknown handles abort before any work starts. `--dry-run` prints
which companies would run, which are skipped as fresh, and the concurrency plan — the
first thing to reach for when a batch misbehaves. `--json-output` routes through
`reporting.output.save_result` so there is one renderer, not two.

- **T3.1** `cli/batch.py` and wiring into `cli/main.py`.
- **T3.2** Validate on a small HTTP-class slice with no LLM cost — e.g. Speechify,
  Cloudbeds, Zscaler, Elastic, Newsela (all Greenhouse). Then one `browser`-class
  agent-less board (Accenture or CommandLink) to exercise the browser bucket without
  agent non-determinism.
- Acceptance: `--dry-run` plan matches expectation; a second immediate run skips
  everything as fresh; `--force` re-runs; killing a run mid-way and re-running reaps
  and resumes; `sqlite3 data/vacantes.db 'select company_slug, count(*) from job_urls
  group by 1'` matches each board's verdict count.

## 8. Phase 4 — Full corpus and operations — **LANDED**

**As built, one deviation and one correction.** The deviation: T4.1 asks for the
ceilings to be tuned, and the measurement said to leave both where they are, so
nothing changed in code. Recording *why* a default survived contact with the
full corpus is the deliverable here, and it lives in `ARCHITECTURE.md` beside
the ceilings it explains.

The correction is worth keeping, because the first reading of the run was wrong.
Grepping the run log for rate-limit evidence appeared to show hits accumulating
as the batch progressed. They were false positives: the pattern was matching
job-posting URLs whose ids happen to contain the digits of an HTTP status, not
provider errors. Re-checked against log levels rather than raw substrings, the
run contains **no provider errors at all**, which inverts the conclusion — the
browser bucket is bounded by memory, not by the LLM provider. A tuning number
derived from the first reading would have been confidently wrong.

**What the run measured.** 116 companies (97 browser-class, 80 of those driving
an agent) in 967 seconds, 116 successes and no failures, 1504 postings stored,
and `company_runs.url_count` equal to `select count(*) from job_urls` for every
one of the 116 — the Phase 3 parity check repeated corpus-wide. Peak Chromium
residency at the default ceiling of 4 was 8214, 8425, and 8406 MB across three
independent runs, so it tracks the ceiling rather than the workload. A ceiling
of 8 cost 12615 MB for a wall time that fell inside the ceiling-4 noise band of
123-141s, meaning the extra slots bought nothing at this corpus size. The HTTP
ceiling has never bound: 19 HTTP-class boards against a ceiling of 20 finished
in 5 seconds.

**What the review then measured.** The run's verdicts: of 60 boards with a human
count, 20 `match`, 25 `under`, 15 `over`; agent-driven boards matched 11 of 31,
agent-less 7 of 15, deterministic API adapters 2 of 14. Re-running all 20
deterministic mismatches and the 3 empty agent boards sequentially through
`vacantes integrate` reproduced the batch's count on every one, so concurrency at
the default ceiling changes no board's result and the mismatches are drift against
stale `expected_jobs`. That is §3.2 observed corpus-wide, and it is recorded in
`ARCHITECTURE.md` beside the ceilings.

**T4.3 was dropped from the plan** before this phase ran — revisiting the
blocked-board dispositions is a judgement call about coverage, not delivery
work, and it belongs to whoever next reads `blockers/INTEGRATION_BLOCKERS.md`.

- **T4.1** Full run; tune the two ceilings against observed memory and provider
  rate-limit behaviour; record the numbers in `ARCHITECTURE.md`.
- **T4.2** Operational runbook in `README.md`: cadence (2–3×/week), the one query that
  answers the product question, `--dry-run` first, backup = copy `data/vacantes.db`.

## 9. Design principles

Single responsibility: the scheduler decides *when*, the worker decides *what one unit
of work means*, the repositories own *every SQL statement*, the strategies stay purely
about *extraction*. Open-closed: an eighth adapter registers in `STRATEGIES` as today
and is classified by one line in `policy.py`, which a test enforces. Interface
segregation: three repository modules because the worker needs the URL and lifecycle
paths and has no business seeing catalog sync. Dependency inversion: the worker programs
against the `ExtractionStrategy` protocol and receives its session factory rather than
importing a global engine — which is what makes §6.6's tests possible without a
database file. Shared code by composition: both entry points call the same coroutine
with the same context; only scheduling and output differ.

## 10. Non-goals

- **Posting history.** Excluded by decision. If wanted later, replace the
  delete-and-insert in §5.5 with an upsert carrying `first_seen_at` / `last_seen_at` —
  a change confined to one repository function.
- **Workflow frameworks** (Prefect, Dagster, Airflow, Temporal). They earn their weight
  with cross-run durable state, task dependency graphs, distributed workers, or a shared
  dashboard. This is one operator, one machine, independent units of work: a semaphore
  and a gather. Revisit on a real need for durable retries surviving process death, or a
  second machine.
- **PostgreSQL**, for the same reason; §5.1 is the migration path.
- **An API or UI** over the database. Queries go straight at the SQLite file.
- **A `--persist` flag on `integrate`.** Plausible convenience; not required here.

## 11. Open questions

Items 1–6 are settled; item 7 is open.

1. **`batch/` vs `runs/`.** Settled: `batch/`. No continuous or watch mode exists or
   is planned, and the name says what the component does today.
2. **Alias lifetime.** Settled: `job-agent-lab` stays indefinitely. It costs one
   `pyproject.toml` line and protects every doc, skill, and habit.
3. **Repository rename timing.** Deferred by choice as T0.9. The new packages were
   pushed under the old remote name, which costs nothing but a later URL change.
4. **`reporting/` placement.** Settled: top-level. Both `integrate` and
   `batch --json-output` render through it.
5. **Concurrency-class naming.** Settled: `browser`/`http`, naming the cost the
   semaphore protects.
6. **Where the retired `ARCHITECTURE_PROPOSAL_R2.md` rationale now lives.** Settled:
   the ten citations were repointed or inlined. The hook execution order is pinned by
   `TestIntegrationOrder` in `tests/snapshots/test_runtime_hooks.py` and implemented
   by `collect_job_links`; the matcher boundary semantics are pinned by
   `test_matcher_rules.py`; the Phenom multi-location rule and the Coveo wire
   contract are stated in their adapters' own docstrings; the Talentbrew mirror's
   authority is the shipped JS matcher, via `test_linkrule_parity.py`. The six
   `TRANSITION.md` section citations in `src/` and `tests/` were repointed at
   `ARCHITECTURE.md` at the same time, so removing this file orphans nothing.
7. **The `spike/` citations.** Closing item 6 surfaced a third class of the same
   problem: twelve docstrings and comments cite files under a `spike/` directory that
   was never committed — SYS plan documents (`SYS_4_PLAN.md`, `SYS_7_TICKET.md`,
   `SYS_13_PLAN.md`, `SYS_16_RESULTS.md`) and evidence captures under
   `spike/evidence/` — in `prompt.py`, `coveo.py`, `company.py`, `probe_board.py`,
   and five test modules. Unlike item 6 these are mostly provenance notes ("seeded
   from this capture"), so the fix is to say what the evidence *was* in a clause and
   drop the path. One `docs:` commit, no code change. Open.

## 12. Task index

| Task | Phase | Done when |
|---|---|---|
| T0.1 | 0 | ✅ Baseline reports for `speechify` and `commandlink` saved |
| T0.2 | 0 | ✅ Package is `src/vacantes`; tests green; both console scripts resolve |
| T0.3 | 0 | ✅ `navigation/` gone; lives at `extraction/dom/agent/`; pre-commit regex updated |
| T0.4 | 0 | ✅ Both share `add_integrate_arguments` |
| T0.5 | 0 | ✅ No "lab" remains in `src/` prose, banner, or `--help` |
| T0.6 | 0 | ✅ Four docs and both skill copies updated; the two intentional per-assistant differences kept and documented |
| T0.7 | 0 | ✅ `test_layering.py` encodes §2.3 from the real graph, and fails a new package that has no allowlist row |
| T0.8 | 0 | ✅ Zero references to non-existent *blocker* docs; `ARCHITECTURE_PROPOSAL_R2.md` citations closed under §11; `spike/` citations open (§11 item 7) |
| T0.9 | 0 | ⏸ Deferred by choice — `origin` is still `ticolab/job-agent-lab` |
| T1.1 | 1 | ✅ `engine.py` pragmas asserted on a real file; `models.py` written; `data/` gitignored |
| T1.2 | 1 | ✅ `migrations/` + `alembic.ini` at root; `env.py` reads `Base.metadata` and `settings.DATABASE_PATH`; `upgrade head` produces §5.3 exactly |
| T1.3 | 1 | ✅ Three repositories per §5.4, plus the `list_company_urls` read helper |
| T1.4 | 1 | ✅ 28 tests on a real SQLite file under `tmp_path`; suite at **878** |
| T1.5 | 1 | ✅ `sqlalchemy>=2.0.0`, `aiosqlite>=0.20.0`, `alembic>=1.13.0` |
| T2.1 | 2 | ✅ `policy.py`: cost classification, `BatchPolicy`, `should_skip` |
| T2.2 | 2 | ✅ `worker.py`: `run_company`, `CompanyOutcome`, the isolation boundary |
| T2.3 | 2 | ✅ `scheduler.py`: `run_batch`, startup reaping, catalog sync, aggregation |
| T2.4 | 2 | ✅ 25 tests on fakes — no browser, no network; ceiling tests verified to fail an unbounded scheduler; suite at **903** |
| T3.1 | 3 | ✅ `cli/batch.py` wired into the dispatcher; selection mandatory; schema preflighted |
| T3.2 | 3 | ✅ Five Greenhouse boards plus one browser-class board run live; every §7 acceptance item observed; suite at **932** |
| T4.1 | 4 | ✅ Full corpus run: 116/116 success, 1504 URLs, 967s; both ceilings measured and kept; numbers in `ARCHITECTURE.md` |
| T4.2 | 4 | ✅ Runbook in `README.md`: cadence, measured cost, the product query, `--dry-run` first, backup by file copy |
