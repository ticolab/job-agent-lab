# job-agent-lab

A research sandbox for **agentic, browser-based job listing extraction**. Instead of
brittle CSS/XPath selectors per site, it drives a [`browser-use`](https://github.com/browser-use/browser-use)
agent (backed by an OpenAI model via LiteLLM) to navigate a career page, apply
location/region filters, reveal all listings, and extract job posting URLs.

The goal of this repo is **robustness testing**: validate the agentic approach
against a large, diverse sample of real career sites (30–50+) to surface edge
cases — dynamic JS, pagination, expandable cards, ATS platforms, etc. — before
the approach is trusted in a production pipeline.

> Origin: extracted from the `tw-data` pipeline's `tools/agent_poc/` PoC (HNT-5).

## How it works

For each company you provide:

- `job_board_url` — the listing page the agent navigates to
- `sample_job_url` — any single job posting; its path prefix (e.g. `/jobs`,
  `/apply`) is derived to identify which links on the board are real jobs

The agent does the messy UI work (filtering, scrolling, dynamic content). A
deterministic custom tool, `extract_job_links`, then runs in the browser via
`page.evaluate` and collects same-origin `<a>` tags matching the path prefix —
so the actual link extraction is exact, not LLM-guessed.

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev              # install deps (incl. dev tooling)
uv run playwright install chromium   # browser for browser-use
cp .env.example .env             # then add your OPENAI_API_KEY
```

## Usage

```bash
# Run all companies in src/job_agent_lab/config.py
uv run job-agent-lab

# Single company (partial name match)
uv run job-agent-lab --company akurey

# Watch the browser, pick a model, cap steps
uv run job-agent-lab --company gap --headed --model gpt-4o --max-steps 25
```

Results are written as timestamped JSON to `./output/` and a summary is printed
to the console (jobs found, elapsed time, agent steps, errors).

### Adding a new company

Companies are integrated through a queue file plus an agent skill, not by editing `config.py` directly. The queue file is your personal, untracked backlog; the skill reads from it, runs the deterministic extractor, captures a regression snapshot, runs the full agent end-to-end, gates the quality checks, and commits the result.

On first use, copy the committed schema example into a real queue file. The example is tracked, the queue itself is gitignored:

```bash
cp new-companies.example.json new-companies.json
```

Append one entry per company you want to integrate. Each entry has five fields; use `null` or angle-bracket placeholders for any value you cannot fill yet, and the skill will refuse to start until they are all real. A blank entry looks like this:

```json
{
  "name": "<Company Name>",
  "aliases": [],
  "job_board_url": "https://<board-host>/<org>",
  "sample_job_url": "https://<board-host>/<org>/<some-posting-id>",
  "expected_jobs": null
}
```

The `expected_jobs` value is the count you see on the board when you open it in a browser and count by hand — it is the target the integration must hit, and the skill will not adjust it to match observed reality. The `aliases` list may stay empty unless the auto-derived handle (name acronym or substring) is awkward to type as `-c <handle>`.

Once at least one entry is filled in, invoke the `integrate-company` skill with the company name (or any alias). The skill handles ground-truth verification, snapshot capture, the live agent run, the quality gates, and a single `feat:` commit covering `config.py` plus the new snapshot directory. On success, it removes the entry from your `new-companies.json` for you.

## Development

```bash
uv run ruff format .       # format
uv run ruff check . --fix  # lint
uv run mypy src            # type-check
uv run pre-commit install  # optional: git hooks
```
