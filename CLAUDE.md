# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`vacantes` answers one question a few times a week: **what job postings are open right now.** It extracts job-posting URLs from a corpus of real career sites, built around one decision — a `browser-use` agent (OpenAI model via LiteLLM) handles the non-deterministic UI work on a career page (filtering by region, revealing dynamic content) while the actual link extraction is **deterministic**: a JS matcher runs in the page and collects same-origin `<a>` tags whose path matches a prefix derived from each company's `sample_job_url`. Never replace the deterministic matcher with LLM-guessed link lists.

Only one of the seven registered strategies drives an agent at all. The other six are plain HTTP against the ATS or search platform backing the board, so "agent" names a mechanism inside `extraction/dom/`, not the system.

Two purposes share one core. The `integrate` CLI onboards and debugs one board at a time and writes a JSON artifact a human reviews before a catalog entry is committed; the `batch` scheduler runs the corpus concurrently and persists the current URL set. Both call the same `extract` coroutine with the same `RunContext`, which is what keeps the integration workflow meaningful as a correctness signal for scheduled runs.

Detailed per-feature design notes (SYS-N history, matcher semantics, adapter wire contracts) live in `TABNINE.md`; architecture rationale in `ARCHITECTURE.md`; known site blocker classes (C1–C22) in `blockers/`. `TRANSITION.md` holds the in-flight restructuring plan and is deleted phase by phase as each one lands.

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

Commit directly to `main` — no feature branches in this repo. Never bypass the pre-commit hooks with `--no-verify`; if an auto-fixer modifies a file, re-stage and commit again.

Commit messages are a **single short sentence, 100 characters max** — subject line only, no body. Reasoning belongs in the code, the design docs, or `blockers/`, not in the commit message.

## Architecture

### Components and the shared kernel

Three components sit over one shared kernel. `extraction/` owns the port and every strategy. `persistence/` owns the database. `batch/` owns concurrency and the per-company unit of work. The kernel — `domain/`, `catalog/`, `settings.py` — is the vocabulary all three speak.

Only `extraction/` exists today, alongside `cli/` and `reporting/`. `persistence/` and `batch/` arrive in later phases of `TRANSITION.md`; the layering allowlist covers the packages actually present and gains a row as each lands.

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

`tests/unit/test_layering.py` parses every module's imports with `ast` and asserts each package imports only from its allowed set, so this is structural rather than a matter of discipline. A strategy cannot write to the database, cannot know a batch is running, and cannot behave differently under the scheduler than under the integration CLI. The allowlist is written from the real graph and may only ever shrink. Because each row is the real graph rather than an intention, widening one — say `extraction → catalog` — is a deliberate allowlist edit that surfaces in review, never something a new import does silently. That `extraction` may reach neither `persistence` nor `batch` is asserted by name as well as by table, so the invariant survives a future edit to the allowlist.

### Strategy port

Every run dispatches through `extraction.get_strategy(company.strategy).extract(company, ctx)`. The port is in `src/vacantes/extraction/base.py`: the `ExtractionStrategy` Protocol, frozen `RunContext`, the `STRATEGIES` registry, and `build_report(...)` — the single author of the output shape.

- `strategy="dom"` (default) — `extraction/dom/strategy.py`. browser-use agent under `GOAL_PROMPT` + the deterministic matcher.
- `strategy="greenhouse" | "phenom" | "talentbrew" | "coveo" | "peopleforce" | "bamboohr"` — `extraction/ats/*.py`. API adapters for boards the DOM path cannot reach or cannot regression-test honestly. No browser and no API key, except `coveo` with `browser_token_key` set, which opens a browser session purely to read a token out of page state. The four agent-only report fields emit `None`.

Adding a strategy requires extending the `StrategyName` literal in `domain/company.py` **and** registering it in `extraction/__init__.py` — a unit test asserts the registry matches the literal, so either half missing fails.

### Package layout (`src/vacantes/`)

- `cli/` — `main.py` is the `vacantes` dispatcher (a table of subcommands, not a branch per subcommand); `integrate.py` holds the argparse surface and the per-company orchestration loop; `batch.py` is the batch subcommand. `settings.py` — defaults (model, max steps, render-settle constants shared by runtime and capture).
- `domain/` — pydantic schema: `Company` (with `strategy`, `paginate`, `hooks`, `pre_filter_urls`), `LinkRule` (`path_prefix`, `min_depth`, `suppress_ancestor_selector`), `RuntimeHooks`, `ExtractionResult`, `TargetRegion` (+ the `COSTA_RICA_LATAM` singleton). Cross-field validators fire at catalog-import time (e.g. hooks require `strategy="dom"`; `pre_filter_urls` must share origin with `job_board_url`).
- `catalog/` — the `COMPANIES` list plus `find_company`/`slugify`. The source of truth for the corpus; persistence projects it and never owns it. Handle resolution iterates in list order, checking alias > acronym > substring — insertion order is authoritative when names collide.
- `extraction/dom/` — `assets/collect_links.js` is the matcher's **single source of truth** (loaded via `importlib.resources` as `EXTRACT_JOB_LINKS_JS`; edit the JS, never an inlined copy). `collector.py` runs it against a live page and owns the pagination walker and hook execution behind the `PageDriver` protocol.
- `extraction/dom/agent/` — the agent wiring, which lives here because `extraction/dom/strategy.py` is its only consumer: `prompt.py` (`GOAL_PROMPT` clause constants + `build_goal_prompt(region)`), `runner.py` (agent/session build), `controller.py` (registers the `extract_job_links` tool).
- `reporting/output.py` — JSON output to `./output/` (gitignored) + terminal summary. Kept top-level rather than CLI-private because both `integrate` and batch's optional JSON output render through it.

### Per-company escape hatches (all inert by default)

When the agent fails a board deterministically, pin the fix on the deterministic side rather than prompt-tweaking per site: `paginate=True` (multi-page walker), `hooks.pre_extract_css` / `hooks.expand_selector` / `hooks.next_control_selector` / `hooks.filter_already_applied`, `pre_filter_urls` (agent-less union over URL variants), `LinkRule.min_depth` / `suppress_ancestor_selector`. Each default is byte-identical to the pre-feature code path. Decision guidance and the motivating board for each knob are in `TABNINE.md`.

### Testing model

Two independent regression corpora, don't conflate them:

- **DOM companies**: frozen page copies under `tests/fixtures/snapshots/<slug>/` (`page.html` + `metadata.json`, optionally `frames/`, `pages/`, `states/`). `tests/snapshots/test_extractor_snapshots.py` replays the production matcher JS in real Chromium and asserts the recorded **unfiltered** count — this validates the matcher, not the agent's filtering. Captures come from `scripts/capture_snapshot.py -c <handle> --expected N` (pass `--paginate` iff the entry has `paginate=True`).
- **API companies**: recorded payloads under `tests/fixtures/api/<ats>/` with respx-mocked tests in `tests/unit/`. Greenhouse filenames are the board *token*, not the company slug.

`tests/snapshots/test_linkrule_parity.py` pins the Talentbrew Python URL-bucketing mirror to the shipped JS matcher — matcher edits must keep both in sync.

### Adding a company

The canonical workflow is the `integrate-company` skill, driven by the untracked `new-companies.json` queue (schema in `new-companies.example.json`). First step is always `scripts/probe_board.py` (read-only reconnaissance: anchor census, frame/shadow map, pagination affordances, platform fingerprint, suggested `Company(...)` snippet). Then: ground-truth verification (`.claude/skills/integrate-company/scripts/extractor_ground_truth.py`), live agent run, snapshot capture, quality gates, one `feat:` commit. `expected_jobs` in the queue is a human-counted target — never adjust it to match observed output.

## Conventions

- Python 3.12+, uv-managed. Ruff (line-length 88, double quotes, rules `E,F,I,W,UP,B,C4,SIM`); mypy strict (`disallow_untyped_defs`) — every function fully annotated.
- Import as `from vacantes.X`; the package lives under `src/`.
- `extraction/dom/agent/prompt.py` and `tests/unit/test_prompt_render.py` are E501-exempt on purpose: prompt clauses and their golden copies are byte-exact single-line literals — never reflow them.
- `TABNINE.md` is the Tabnine CLI's guidance file; its MCP-only tool-routing rule applies to Tabnine, not Claude Code. Keep the two files' project facts consistent when architecture changes.
- The `integrate-company` skill is maintained in two copies, `.claude/skills/` and `.tabnine/agent/skills/`. They are identical except for two deliberate per-assistant adaptations: the Playwright MCP tool prefix and the guidance file each cites for the commit convention. Keep every other change in sync across both.
