# vacantes

`vacantes` answers one question: **what job postings are open right now.** It
extracts job-posting URLs from a corpus of real career sites. Instead of brittle
CSS/XPath selectors per site, it drives a
[`browser-use`](https://github.com/browser-use/browser-use) agent (backed by an
OpenAI model via LiteLLM) to navigate a career page, apply location/region
filters, reveal all listings, and extract job posting URLs.

The design rests on one split: **navigation is non-deterministic, extraction is
deterministic.** The agent absorbs the variance in how sites present their
listings; a single JavaScript matcher decides what counts as a job link. That
keeps every count auditable, and it means matcher behaviour can be frozen and
regression-tested offline across the whole corpus in seconds.

Only one of the seven extraction strategies drives an agent. The other six talk
plain HTTP to the ATS or search platform backing the board, for cases where the
rendered page cannot be reached or cannot yield an honest regression artifact.

> Origin: extracted from the `tw-data` pipeline's `tools/agent_poc/` PoC (HNT-5).

## How it works

Each company in the catalog supplies two URLs. The `job_board_url` is the listing
page the agent navigates to. The `sample_job_url` is any single job posting; its
path prefix (for example `/jobs` or `/apply`) is derived to identify which links
on the board are real jobs.

The agent does the messy UI work of filtering, scrolling, and revealing dynamic
content. A deterministic custom tool, `extract_job_links`, then runs in the
browser via `page.evaluate` and collects same-origin `<a>` tags matching the path
prefix, so the actual link extraction is exact rather than LLM-guessed.

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev                  # install deps (incl. dev tooling)
uv sync --group test                 # pytest, pytest-playwright, respx
uv run playwright install chromium   # browser for browser-use and snapshot tests
cp .env.example .env                 # then add your OPENAI_API_KEY
```

`OPENAI_API_KEY` is required only for live agent runs. The test suite, every API
strategy, and companies configured with `pre_filter_urls` all run without it.

## Usage

Two console scripts are installed. `vacantes` is the dispatcher; `job-agent-lab`
is a permanent alias for the integrate workflow, kept because every doc and skill
is built around it. The two are equivalent.

```bash
# Run every company in src/vacantes/catalog/companies.py
uv run job-agent-lab

# Single company (matched by alias, then acronym, then substring)
uv run job-agent-lab --company akurey

# Watch the browser, pick a model, cap steps
uv run job-agent-lab --company gap --headed --model gpt-4o --max-steps 25

# The same run through the dispatcher
uv run vacantes integrate -c akurey
```

Results are written as timestamped JSON to `./output/` and a summary is printed
to the console reporting jobs found, elapsed time, agent steps, and errors. The
`--strict` flag exits non-zero if any run's verdict is not `match`, which makes
the command usable as a gate.

### Running a batch

The commands above onboard and debug one board at a time. `vacantes batch` is
the operational counterpart: it runs the corpus concurrently and writes the
current job-URL set to a SQLite database, which is the artifact that answers
the question at the top of this file.

Apply the migrations before the first run. Pointing at a path that does not
exist creates an empty database quite happily, so the batch checks the schema
first and names the tables it is missing rather than failing part-way through.
The database defaults to `data/vacantes.db`; set the `VACANTES_DB` shell variable
to move it, and both the batch and Alembic will follow. `--database PATH` moves a
single run instead, in which case migrate that file with
`VACANTES_DB=PATH uv run alembic upgrade head`.

```bash
uv run alembic upgrade head                      # once, and after any schema change
uv run vacantes batch --dry-run --all            # plan only, writes nothing
uv run vacantes batch -c speechify -c cloudbeds  # two named boards
uv run vacantes batch --all                      # the whole corpus
```

A selection is always required. There is no bare `vacantes batch` meaning
"everything", because a full run drives hundreds of boards and most of them
cost a real browser and a paid model, so the whole corpus has to be asked for
by name. Handles resolve exactly as they do for `integrate`, and an unknown one
aborts before any work starts.

Start with `--dry-run` whenever a batch behaves unexpectedly. It prints which
companies would run, which are skipped because they already succeeded recently,
and the concurrency ceilings, without writing anything at all.

Companies that succeeded inside the freshness window are skipped, so re-running
after a partial failure retries only what is owed, and `--force` ignores the
window. A board that failed is recorded as a failure in the database rather
than signalled through the exit code, so a batch that ran to completion exits
zero even when some of its boards did not succeed.

One query answers the product question, and the run history beside it is where
to look when a count seems wrong:

```bash
sqlite3 data/vacantes.db 'select company_slug, url from job_urls order by company_slug'
sqlite3 data/vacantes.db 'select company_slug, status, url_count, verdict from company_runs'
```

### Adding a new company

Companies are integrated through a queue file plus an agent skill, not by editing
the catalog directly. The queue file is your personal, untracked backlog. The
skill reads from it, runs the deterministic extractor, captures a regression
snapshot, runs the full agent end-to-end, gates the quality checks, and commits
the result.

On first use, copy the committed schema example into a real queue file. The
example is tracked, the queue itself is gitignored:

```bash
cp new-companies.example.json new-companies.json
```

Append one entry per company you want to integrate. Each entry has five fields.
Use `null` or angle-bracket placeholders for any value you cannot fill yet; the
skill refuses to start until they are all real. A blank entry looks like this:

```json
{
  "name": "<Company Name>",
  "aliases": [],
  "job_board_url": "https://<board-host>/<org>",
  "sample_job_url": "https://<board-host>/<org>/<some-posting-id>",
  "expected_jobs": null
}
```

The `expected_jobs` value is the count you see on the board when you open it in a
browser and count by hand. It is the target the integration must hit, and the
skill will not adjust it to match observed reality. The `aliases` list may stay
empty unless the auto-derived handle is awkward to type as `-c <handle>`.

Once at least one entry is filled in, invoke the `integrate-company` skill with
the company name or any alias. The skill handles ground-truth verification,
snapshot capture, the live agent run, the quality gates, and a single `feat:`
commit covering `catalog/companies.py` plus the new snapshot directory. On
success, it removes the entry from your `new-companies.json` for you.

## Development

```bash
uv run ruff format .                       # format
uv run ruff check . --fix                  # lint
uv run mypy src scripts tests              # type-check
uv run --group test pytest tests/          # full suite
uv run --group test pytest tests/unit/     # browser-free subset
uv run pre-commit install                  # optional: git hooks
```

Design documents are kept close to the code. `ARCHITECTURE.md` holds the
big-picture map and the invariants. `CLAUDE.md` and `TABNINE.md` are guidance
files for the two AI assistants used on this repo and carry the same project
facts. `blockers/INTEGRATION_BLOCKERS.md` catalogues the site behaviours that
resist extraction, referenced as C1–C22 throughout the codebase.
