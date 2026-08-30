# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A research sandbox for **agentic, browser-based job listing extraction**. A `browser-use` agent (OpenAI model via LiteLLM) handles the non-deterministic UI work on company career pages — filtering by region, revealing dynamic content — while the actual link extraction is **deterministic**: a JS matcher runs in the page and collects same-origin `<a>` tags whose path matches a prefix derived from each company's `sample_job_url`. Never replace the deterministic matcher with LLM-guessed link lists.

The goal is robustness testing across a large sample of real career sites (dynamic JS, pagination, ATS platforms) before the approach is trusted in a production pipeline.

Detailed per-feature design notes (SYS-N history, matcher semantics, adapter wire contracts) live in `TABNINE.md`; architecture rationale in `ARCHITECTURE.md`; known site blocker classes (C1–C19) in `blockers/`.

## Commands

```bash
uv sync --extra dev                  # runtime + dev deps
uv sync --group test                 # pytest, pytest-playwright, respx
uv run playwright install chromium   # needed by live agent AND snapshot tests

uv run job-agent-lab                 # run all companies in the catalog
uv run job-agent-lab -c akurey       # one company (alias > acronym > substring match)
uv run job-agent-lab -c gap --headed --model gpt-4o --max-steps 25

uv run ruff format . && uv run ruff check . --fix
uv run mypy src scripts tests
uv run --group test pytest tests/                # full suite
uv run --group test pytest tests/unit/           # browser-free (schema, adapters, prompt)
uv run --group test pytest tests/snapshots/      # needs Chromium (frozen-DOM regression)
uv run --group test pytest tests/unit/test_coveo.py -k pagination   # single test
```

`OPENAI_API_KEY` in `.env` is required only for live `strategy="dom"` agent runs. Unit tests, snapshot tests, API strategies, and `pre_filter_urls` companies all run without it.

Pre-commit gates: ruff, mypy, and the full test suite runs on any change under `src/job_agent_lab/{extraction,navigation,domain}/` or `tests/`.

Commit directly to `main` — no feature branches in this repo. Never bypass the pre-commit hooks with `--no-verify`; if an auto-fixer modifies a file, re-stage and commit again.

Commit messages are a **single short sentence, 100 characters max** — subject line only, no body. Reasoning belongs in the code, the design docs, or `blockers/`, not in the commit message.

## Architecture

### Strategy port

Every run dispatches through `extraction.get_strategy(company.strategy).extract(company, ctx)`. The port is in `src/job_agent_lab/extraction/base.py`: the `ExtractionStrategy` Protocol, frozen `RunContext`, the `STRATEGIES` registry, and `build_report(...)` — the single author of the output shape.

- `strategy="dom"` (default) — `extraction/dom/strategy.py`. browser-use agent under `GOAL_PROMPT` + the deterministic matcher.
- `strategy="greenhouse" | "phenom" | "talentbrew" | "coveo"` — `extraction/ats/*.py`. API adapters for boards unreachable via the DOM path. No browser, no API key; the four agent-only report fields emit `None`.

Adding a strategy requires extending the `StrategyName` literal in `domain/company.py` **and** registering it in `extraction/__init__.py` — a unit test asserts the registry matches the literal, so either half missing fails.

### Package layout (`src/job_agent_lab/`)

- `cli.py` — argparse entry point (`job-agent-lab` script); `settings.py` — defaults (model, max steps, render-settle constants shared by runtime and capture).
- `domain/` — pydantic schema: `Company` (with `strategy`, `paginate`, `hooks`, `pre_filter_urls`), `LinkRule` (`path_prefix`, `min_depth`, `suppress_ancestor_selector`), `RuntimeHooks`, `ExtractionResult`, `TargetRegion` (+ the `COSTA_RICA_LATAM` singleton). Cross-field validators fire at catalog-import time (e.g. hooks require `strategy="dom"`; `pre_filter_urls` must share origin with `job_board_url`).
- `catalog/` — the `COMPANIES` list plus `find_company`/`slugify`. Handle resolution iterates in list order, checking alias > acronym > substring — insertion order is authoritative when names collide.
- `extraction/dom/` — `assets/collect_links.js` is the matcher's **single source of truth** (loaded via `importlib.resources` as `EXTRACT_JOB_LINKS_JS`; edit the JS, never an inlined copy). `collector.py` runs it against a live page and owns the pagination walker and hook execution behind the `PageDriver` protocol.
- `navigation/` — `prompt.py` (`GOAL_PROMPT` clause constants + `build_goal_prompt(region)`), `runner.py` (agent/session build), `controller.py` (registers the `extract_job_links` tool).
- `reporting/output.py` — JSON output to `./output/` (gitignored) + terminal summary.

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
- Import as `from job_agent_lab.X`; the package lives under `src/`.
- `navigation/prompt.py` and `tests/unit/test_prompt_render.py` are E501-exempt on purpose: prompt clauses and their golden copies are byte-exact single-line literals — never reflow them.
- `TABNINE.md` is the Tabnine CLI's guidance file; its MCP-only tool-routing rule applies to Tabnine, not Claude Code. Keep the two files' project facts consistent when architecture changes.
