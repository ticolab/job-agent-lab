# TABNINE.md

Guidance file for the Tabnine CLI when working in this repository. Keep this file's project facts consistent with `CLAUDE.md`; both are guidance surfaces for different AI assistants and neither should drift from the other when the architecture changes.

## Project Overview

`vacantes` answers one question a few times a week: **what job postings are open right now.** It extracts job-posting URLs from a corpus of real career sites, and it exists to stress-test one design decision — **navigation is non-deterministic, extraction is deterministic** — against a large, diverse corpus before the approach is trusted in a production pipeline.

For each configured company, a `browser-use` agent (OpenAI model routed via LiteLLM) drives the career page: loads the board, applies a location filter, reveals lazily-mounted content, and expands collapsed sections. The agent never decides what counts as a job link — that judgement belongs to a single JavaScript matcher (`collect_links.js`) which runs inside the page via `page.evaluate` and returns same-origin `<a>` tags matching a per-company path prefix. A regression harness replays the shipped matcher against frozen page snapshots so counting behaviour can be validated offline in seconds across the whole corpus.

Only one of the seven registered strategies drives an agent at all; the other six are plain HTTP against the ATS or search platform backing the board. "Agent" therefore names a mechanism inside `extraction/dom/`, not the system.

Read `ARCHITECTURE.md` for the design rationale and invariants. Read `blockers/INTEGRATION_BLOCKERS.md` for the catalogue of site behaviours that resist extraction (referenced as C1–C22 throughout the codebase). Read `CLAUDE.md` for the equivalent guidance surface aimed at Claude Code. Read `TRANSITION.md` for the in-flight restructuring plan; it is transient and each phase's section is deleted once that phase lands and its durable rationale has graduated into `ARCHITECTURE.md` and this file.

## Building and Running

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). All commands run through `uv`.

Setup the environment and Chromium (needed for both the live agent and the snapshot regression suite):

```bash
uv sync --extra dev                  # runtime + dev deps
uv sync --group test                 # pytest, pytest-playwright, respx
uv run playwright install chromium
cp .env.example .env                 # add OPENAI_API_KEY for live DOM runs
```

Two console scripts are registered. `vacantes` is the dispatcher, with `integrate` and `batch` subcommands. `job-agent-lab` is a frozen alias pointing straight at the integrate entry point rather than through the dispatcher, so its argument surface is the pre-rename CLI's by construction. Both populate their parser from the same `add_integrate_arguments` function, so the two surfaces cannot drift. Keep the alias working — every doc, skill, and habit is built on it.

Run the extractor end-to-end. The `-c` flag matches by alias, then acronym, then substring against `catalog.COMPANIES`:

```bash
uv run job-agent-lab                                        # every company
uv run job-agent-lab -c akurey                              # one company
uv run job-agent-lab -c gap --headed --model gpt-4o --max-steps 25
uv run vacantes integrate -c akurey                         # identical, via the dispatcher
```

Results are written as timestamped JSON to `./output/` (gitignored) and a summary is printed to the console. The `--strict` flag exits non-zero if any run's `metadata.verdict` is not `"match"`.

The `OPENAI_API_KEY` environment variable is required only for live `strategy="dom"` agent runs. Unit tests, snapshot tests, all API strategies (`greenhouse`, `phenom`, `talentbrew`, `coveo`, `peopleforce`, `bamboohr`), and `pre_filter_urls` companies all run without it.

Format, lint, type-check, and test:

```bash
uv run ruff format . && uv run ruff check . --fix
uv run mypy src scripts tests
uv run --group test pytest tests/                # full suite
uv run --group test pytest tests/unit/           # browser-free
uv run --group test pytest tests/snapshots/      # needs Chromium
```

Pre-commit gates ruff, mypy, and the full test suite on any change under `src/vacantes/{extraction,domain}/` or `tests/`. The mypy hook type-checks only the *staged* files and is stricter than the repo-wide run, so a green `uv run mypy src scripts tests` is not a guarantee that the commit passes.

## Architecture

### Components and the shared kernel

Three components sit over one shared kernel. `extraction/` owns the port and every strategy. `persistence/` owns the database. `batch/` owns concurrency and the per-company unit of work. The kernel — `domain/`, `catalog/`, `settings.py` — is the vocabulary all three speak.

Only `extraction/` exists today, alongside `cli/` and `reporting/`. `persistence/` and `batch/` arrive in later phases of `TRANSITION.md`.

Dependencies point inward and never cycle:

```
Enforced today by tests/unit/test_layering.py:

  cli        → catalog, domain, extraction, reporting, settings
  extraction → domain, settings
  reporting  → catalog
  catalog    → domain
  settings   → (nothing)
  domain     → (nothing inside vacantes)

Added to the allowlist as each component lands:

  persistence → domain, settings
  batch       → catalog, domain, extraction, persistence, reporting, settings
```

`tests/unit/test_layering.py` parses every module's imports with `ast` and asserts each package imports only from its allowed set, which makes this structural rather than a matter of discipline. A strategy cannot write to the database, cannot know a batch is running, and cannot behave differently under the scheduler than under the integration CLI. The allowlist is written from the real graph, covers the packages actually present, and may only ever shrink. Because each row is the real graph rather than an intention, widening one — say `extraction → catalog` — is a deliberate allowlist edit that surfaces in review, never something a new import does silently. That `extraction` may reach neither `persistence` nor `batch` is asserted by name as well as by table, so the invariant survives a future edit to the allowlist.

The reason this matters is that two entry points share one core. The `integrate` CLI iterates sequentially and writes a JSON artifact a human reviews before committing a catalog entry; the batch scheduler runs the corpus concurrently and persists the current URL set. Both call the same `extract` coroutine with the same `RunContext`. If they could diverge, the integration workflow would stop being a correctness signal for scheduled runs.

### The central split

Every run dispatches through one port: `extraction.get_strategy(company.strategy).extract(company, ctx)`. The port is defined in `src/vacantes/extraction/base.py` and consists of the `ExtractionStrategy` Protocol, the frozen `RunContext` (model, headless flag, step cap, target region), the `STRATEGIES` registry, and `build_report(...)` — the single author of the output shape.

Two families implement the port. `strategy="dom"` (the default) runs the browser-use agent under `GOAL_PROMPT` alongside the deterministic matcher. The six API adapters — `greenhouse`, `phenom`, `talentbrew`, `coveo`, `peopleforce`, `bamboohr` — live under `extraction/ats/` and bypass the browser entirely, querying the ATS or search platform that backs the board and filtering the returned records by region. Each API strategy earns its module when the DOM path cannot yield an honest regression artifact or cannot reach the postings at all. The one exception to "no browser" is `coveo` with `browser_token_key` set, which opens a browser session purely to read a search token out of page state.

Adding a new strategy requires extending the `StrategyName` literal in `domain/company.py` **and** registering the implementation in `extraction/__init__.py`. A unit test asserts the two sets are equal, so shipping half the change fails loudly.

### Package layout

The `src/vacantes/` tree is organized so each package owns exactly one concern. The CLI lives in `cli/`: `main.py` is the `vacantes` dispatcher (a table of subcommands rather than a branch per subcommand), `integrate.py` holds the argparse surface and the per-company orchestration loop, and `batch.py` is the batch subcommand. Runtime defaults live in `settings.py` (`DEFAULT_MODEL`, `DEFAULT_MAX_STEPS`, `OUTPUT_DIR`, plus the render-settle constants shared by runtime and capture).

The `domain/` package holds the pydantic schema: `Company` with its `strategy`, `paginate`, `hooks`, and `pre_filter_urls` knobs; `LinkRule` with `path_prefix`, `min_depth`, and `suppress_ancestor_selector`; `RuntimeHooks`; `ExtractionResult`; and `TargetRegion` together with the `COSTA_RICA_LATAM` singleton. Cross-field validators fire at catalog-import time — hooks require `strategy="dom"`, `pre_filter_urls` must share origin with `job_board_url`, and API strategies require or forbid their tenant config exactly as documented in each class docstring.

The `catalog/` package supplies the `COMPANIES` list plus `find_company`/`slugify` helpers. It is the source of truth for the corpus; the persistence layer projects it and never owns it. Handle resolution iterates in list order, checking alias, then acronym, then substring; insertion order is authoritative when names collide.

The `extraction/dom/` package owns the deterministic matcher. `assets/collect_links.js` is the single source of truth — loaded once at import time via `importlib.resources` as `EXTRACT_JOB_LINKS_JS`. Edit the JS, never an inlined copy. `collector.py` runs it against a live page and owns the pagination walker and hook execution behind the `PageDriver` protocol.

The `extraction/dom/agent/` package holds the agent wiring, and it lives under `extraction/dom/` because `extraction/dom/strategy.py` is its only consumer: the prompt in `prompt.py` (`GOAL_PROMPT` clause constants plus `build_goal_prompt(region)`), the agent/session build in `runner.py`, and the tool registration in `controller.py` (which registers the `extract_job_links` tool the agent calls).

The `reporting/output.py` module writes JSON output to `./output/` and prints the terminal summary. It never authors the report shape itself; that lives in `build_report`. It stays top-level rather than CLI-private because both `integrate` and batch's optional JSON output render through it.

### The deterministic matcher

The matcher scans the top document, descends into every open shadow root, and enters every same-origin frame it can reach — guarded so inaccessible frames are skipped rather than fatal. Each surviving anchor is gated on CSS visibility. An anchor must be same-origin and match one of two URL shapes: the id sits in the path (`/jobs/12345-engineer`), or the id sits in the query on the prefix itself (`/careers/requirements/?pId=180`). When both shapes appear on one page the path bucket wins and the query bucket is discarded, because a query on a listing root is usually a filter facet rather than a posting.

Boards that need more discrimination get it through data, never through a site-specific code branch. `LinkRule.path_prefix` overrides the derived prefix, `LinkRule.min_depth` requires the id-in-path tail to be at least N segments deep, and `LinkRule.suppress_ancestor_selector` drops anchors inside a named container. Each field defaults to a value that leaves matcher behaviour byte-identical.

### Per-company escape hatches

When the agent fails a board deterministically, pin the fix on the deterministic side rather than prompt-tweaking per site. Every knob defaults to a value byte-identical to the pre-feature code path. The `paginate` boolean turns on the multi-page walker. The four `RuntimeHooks` fields (`pre_extract_css`, `expand_selector`, `next_control_selector`, `filter_already_applied`) each address a specific board's failure mode with decision guidance and motivating board named in the field's docstring. The `pre_filter_urls` tuple runs an agent-less union over URL variants for boards whose location filter can only be applied via URL. Decision guidance for each knob lives inline in `domain/company.py`.

## Testing model

Two independent regression corpora exist, and they must not be conflated.

DOM-strategy companies contribute frozen page copies under `tests/fixtures/snapshots/<slug>/` with `page.html` plus `metadata.json`, optionally with `frames/`, `pages/`, or `states/` subdirectories (a state may carry its own `states/state-N-frames/` and, for declaring boards that also paginate, `states/state-N-pages/` sidecars — Accenture is the first). `tests/snapshots/test_extractor_snapshots.py` replays the production matcher JS in real Chromium and asserts the recorded **unfiltered** count. This validates the matcher's counting, not the agent's filtering.

API-strategy companies contribute recorded payloads under `tests/fixtures/api/<ats>/` with respx-mocked tests in `tests/unit/`. For Greenhouse, filenames are the board *token*, not the company slug.

Fixtures freeze the **unfiltered** listing. The region-filtered target lives on the `Company.expected_jobs` field and is validated by the live run against the report layer's verdict. Conflating them would produce failures that localize to neither.

`tests/snapshots/test_linkrule_parity.py` pins the Talentbrew Python URL-bucketing mirror to the shipped JS matcher — matcher edits must keep both in sync.

Captures are produced by `scripts/capture_snapshot.py -c <handle> --expected N` (pass `--paginate` if and only if the entry has `paginate=True`).

## Adding a company

The canonical workflow is the `integrate-company` skill, driven by the untracked `new-companies.json` queue whose schema is `new-companies.example.json`. First step is always `scripts/probe_board.py` — read-only reconnaissance that produces an anchor census, frame/shadow map, pagination affordances, platform fingerprint, and a suggested `Company(...)` snippet. Then follow the fixed pipeline: ground-truth verification, live agent run, snapshot capture, quality gates, and one `feat:` commit covering `catalog/companies.py` plus the new snapshot directory.

The `expected_jobs` value is a human-counted target and is **never** adjusted to match observed output. Failure at any stage is reported as a blocker with evidence in `blockers/INTEGRATION_BLOCKERS.md` rather than worked around by loosening the target.

## Development Conventions

Python 3.12+, uv-managed. Ruff enforces line-length 88, double quotes, and rules `E,F,I,W,UP,B,C4,SIM`. Mypy strict (`disallow_untyped_defs`) means every function is fully annotated. Import package modules as `from vacantes.X`; the package lives under `src/`.

Two files are E501-exempt on purpose: `extraction/dom/agent/prompt.py` and `tests/unit/test_prompt_render.py`. Prompt clauses and their golden copies are byte-exact single-line literals — never reflow them.

Commit directly to `main`; no feature branches. Never bypass the pre-commit hooks with `--no-verify`; if an auto-fixer modifies a file, re-stage and commit again. Commit messages are a single short sentence, 100 characters max, subject line only, no body. Reasoning belongs in the code, the design docs, or `blockers/`, not in the commit message.

Prefer clean abstractions in the design over per-site code branches. Every opt-in knob defaults to inert so adding one cannot change any existing board's behaviour. Per-board differences live in configuration, not in code — no company name, host, or posting id ever appears in the matcher.

## Tabnine-specific guidance

### Remote codebase research routing

When a task involves researching, investigating, exploring, or searching code in remote repositories (i.e., **not** the local workspace), you MUST delegate the task to the `remote-codebase-investigator` subagent. Do NOT call `tabnine-context__*` MCP tools directly — always route through the subagent instead. This keeps the main-session context lean and prevents remote exploration from polluting the local investigation record.

### Skills

The `integrate-company` skill lives at `.tabnine/agent/skills/integrate-company/SKILL.md`. Activate it when the user names a company they want integrated into the corpus, when debugging why an already-configured company returns the wrong job count, or when troubleshooting zero/short/over-count extraction at integration time. The full entry (name, aliases, job_board_url, sample_job_url, expected_jobs) is read from `new-companies.json` at the repo root, which the user populates from the committed `new-companies.example.json` template.

The skill is maintained in two copies, `.claude/skills/integrate-company/` and `.tabnine/agent/skills/integrate-company/`. They are identical except for two deliberate per-assistant adaptations: the Playwright MCP tool prefix (`mcp_playwright_` here, `mcp__playwright__` for Claude Code) and the guidance file each cites for the commit convention. Keep every other change in sync across both copies.

### Playwright MCP

The `.mcp.json` at the repo root registers `@playwright/mcp` with the `devtools` capability. This is the reconnaissance surface for exploring a live career board when the deterministic `scripts/probe_board.py` output is not enough. Prefer the probe script for anything reproducible; use the Playwright MCP for one-shot investigative work.
