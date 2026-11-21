# Job Discovery

`job-discovery` answers one question: **what job postings are open right now.** It
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

Only one of the seven discovery strategies drives an agent. The other six talk
plain HTTP to the ATS or search platform backing the board.

Three sibling packages process discovered postings. `job_eligibility` exposes a
one-shot eligibility contract: one prompt, a strict four-field response
(`decision`, `reason`, `supporting_evidence`, `conflicting_evidence`), structural
validation, and normalized-exact evidence grounding. `job_extractor` validates
structured job facts, while `job_pipeline` owns content acquisition, model calls,
durable decisions, history, and orchestration. Extraction runs only for jobs
whose current decision is eligible under the current eligibility contract.

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

`OPENAI_API_KEY` is required for live discovery agent runs and for pipeline
`eligibility`, `extraction`, or `reprocess` commands that select an OpenAI model.
Anthropic pipeline models require `ANTHROPIC_API_KEY`. Both stage dry runs,
`preview`, `show`, tests, offline `ReplayCaller` and evaluator replay, API
discovery strategies, and `pre_filter_urls` companies need no provider key.
`scripts/evaluate_leaf.py --record` and `scripts/replay_eligibility.py run` need a
provider key only when a required artifact is missing and a live call is needed.
`scripts/evaluate_leaf.py --reasoning-effort LEVEL` evaluates a reasoning model:
the caller sends that effort instead of `temperature=0`, and recordings, reason
reviews, and freeze records are bound to the effort as a separate candidate.
The pipeline's `eligibility`, `extraction`, and `reprocess` commands accept the same
option, for example `job-pipeline eligibility --all -m gpt-5.4-mini-2026-03-17
--reasoning-effort medium`, and record it on the run and each attempt (null means
`temperature=0`). Eligibility defaults to `gpt-5.4-mini-2026-03-17` at `medium`
effort; an explicit `--model` drops that default effort. Extraction defaults to
`gpt-4.1-mini` with no effort.

## Usage

Three console scripts are installed. `job-discovery` is the discovery dispatcher,
`job-agent-lab` is its permanent integrate alias, and `job-pipeline` is the
posting-processing dispatcher.

```bash
# Run every company in src/job_discovery/catalog/companies.py
uv run job-agent-lab

# Single company (matched by alias, then acronym, then substring)
uv run job-agent-lab --company akurey

# Watch the browser, pick a model, cap steps
uv run job-agent-lab --company gap --headed --model gpt-4o --max-steps 25

# The same run through the dispatcher
uv run job-discovery integrate -c akurey
```

Results are written as timestamped JSON to `./output/` and a summary is printed
to the console reporting jobs found, elapsed time, agent steps, completion, and
errors. There is no verdict field or `--strict` mode. Onboarding compares the
saved report with queue evidence externally.

### Running a scan

The commands above onboard and debug one board at a time. `job-discovery scan` is
the operational counterpart: it invokes the internal batch scheduler to run the
corpus concurrently and writes the current job-URL set to a SQLite database,
which is the artifact that answers the question at the top of this file.

Apply the migrations before the first scan. Pointing at a path that does not
exist creates an empty database quite happily, so the scan checks the schema
first and names the tables it is missing rather than failing part-way through.
The database defaults to `data/job-discovery.db`; set the `JOB_DISCOVERY_DB` shell variable
to move it, and both the scan and Alembic will follow. `--database PATH` moves a
single scan instead, in which case migrate that file with
`JOB_DISCOVERY_DB=PATH uv run alembic upgrade head`.

```bash
uv run alembic upgrade head                     # once, and after any schema change
uv run job-discovery scan --dry-run --all        # plan only, writes nothing
uv run job-discovery scan -c speechify -c cloudbeds  # two named boards
uv run job-discovery scan --all                  # the whole corpus
```

A selection is always required. There is no bare `job-discovery scan` meaning
"everything", because a full scan drives hundreds of boards and most of them
cost a real browser and a paid model, so the whole corpus has to be asked for
by name. Handles resolve exactly as they do for `integrate`, and an unknown one
aborts before any work starts.

Start with `--dry-run` whenever a scan behaves unexpectedly. It prints which
companies would run, which are skipped because they already succeeded recently,
and the concurrency ceilings, without writing anything at all.

Companies that succeeded inside the freshness window are skipped, so re-running
after a partial failure retries only what is owed, and `--force` ignores the
window. A board that failed is recorded as a failure in the database rather
than signalled through the exit code, so a scan that ran to completion exits
zero even when some of its boards did not succeed.

One query answers the product question, and the run history beside it is where
to look when a count seems wrong:

```bash
sqlite3 data/job-discovery.db 'select company_slug, url from job_urls order by company_slug'
sqlite3 data/job-discovery.db 'select company_slug, status, url_count, error_message, started_at, finished_at from company_runs'
```

### Daily processing workflow

Run the stages separately, inspecting the extraction backlog between them:

```bash
uv run alembic upgrade head
uv run job-discovery scan --all
uv run job-pipeline preview --all --by-company
uv run job-pipeline eligibility --all
uv run job-pipeline preview --all --by-company
uv run job-pipeline extraction --all
uv run python scripts/verify_grounding.py
uv run python scripts/replay_eligibility.py run --run RUN_ID --label BASELINE
```

`job-pipeline run` is retired: any arguments exit 2 with a message naming
`eligibility` and `extraction`, without opening a database.

`eligibility` observes selected discovery URLs, acquires content, and makes one
`gpt-4.1-mini` call that reads the complete posting and returns the decision, a
one-line reason, supporting evidence, and conflicting evidence. Only the
canonical missing-information `unknown` may cite no evidence; every other
response needs at least one supporting excerpt. Every excerpt must occur in the
posting under normalized-exact matching (Unicode, whitespace, case, dash, and
approved layout separators normalize; paraphrases and reordered or combined text
do not), and duplicates or excerpts placed in both buckets are rejected. The
decision, reason, both evidence buckets, contract version, and prompt hash
persist atomically with the attempt finish and job transition; an eligible
decision finishes as `ready_for_extraction`, releases the lease, and gives
extraction a fresh attempt budget. A decision is reused instead of calling the
model only when it is current for the same content, contract version, and prompt
hash.

Grounding proves that each quote exists, not that the model read it correctly.
The model authors the semantic judgment; semantic quality is established by
reviewed evidence obligations, five-replay evaluation, and manual reason review,
not by runtime checks.

`extraction` processes current-snapshot `ready_for_extraction` jobs and due
`retry` jobs at `extraction`. Snapshot membership is read-only and follows merged
aliases to their canonical jobs; it neither observes URLs nor syncs the catalog.
The command still writes run, attempt, and result history. It constructs no HTTP
client or browser and rejects their concurrency flags. `--json-output DIR` is
available on `extraction` and `reprocess`, not `eligibility`.

A ready job outside the snapshot stays ready until it reappears. Unobserved URLs
are omitted by extraction. Missing current content, a missing or non-eligible
current decision, a decision on different content, or an eligible decision made
under an earlier eligibility contract fails immediately with `missing_checkpoint`,
without a model call or retry. Clear stale-contract ready jobs with
`reprocess --from-phase eligibility` before running extraction.

`preview` reports eligibility work, extraction backlog, in-progress jobs, terminal
dispositions, the current eligibility contract, and the number of ready jobs whose
current decision predates it, with separate would-process counts capped by
`--limit`. `--no-observe` reads a transactionally consistent SQLite online-backup
snapshot without copying database or WAL files separately.

```bash
uv run job-pipeline eligibility --dry-run --all
uv run job-pipeline extraction --dry-run --all
```

`eligibility --dry-run` observes and may create jobs or update sources and
last-seen timestamps. `extraction --dry-run` does not observe or write rows.
Neither dry run reaps attempts or creates pipeline runs.

`attempt_count` counts attempts in the current stage, not lifetime attempts.
Each stage has three attempts by default; `internal` failures are capped at two.
Retry delays are one then two days by default, resuming from `next_phase`.
The eligibility hand-off gives extraction a fresh budget; historical attempts
remain intact, and migration does not reset existing extraction retries.

The active attempt is an ownership lease, so stale workers cannot overwrite a
newer owner. Startup reaping closes stale leased attempts as `interrupted` and
makes their jobs due for retry, scoped by `next_phase`: eligibility reaps only
`fetch`/`eligibility`, extraction only `extraction`, even outside the selection.
Both use `4 × --timeout-seconds` as the stale bound. A timeout bounds each active
network or model step, not waiting for its slot. Claimed jobs are capped at
`http_concurrency + model_concurrency` for eligibility and `model_concurrency`
for extraction. These ceilings are per invocation, not shared across processes.

Different stages can overlap, but there is no cross-company snapshot transaction:
a concurrent scan or eligibility observation can change what a planner sees.
Claims prevent duplicate ownership; avoid overlapping same-stage runs with
different timeouts, since the shorter stale bound can reap a live attempt.
Individual job failures still exit 0. Ctrl-C exits 130; recovery requires a later
invocation of the owning stage after its stale bound has passed.

`reprocess` requires an audit reason and preserves history. Starting from `fetch`
or `eligibility` stops at `ready_for_extraction`; then run `extraction`. Starting
from `extraction` uses stored checkpoints only. Explicit reprocessing can target
jobs outside the snapshot, but a subsequent ordinary extraction cannot; use
`reprocess --from-phase extraction` for that case. `show` prints audit metadata
and accepted results, including attempts in the current stage and each attempt's
`run_stage`/`run_trigger`, never stored posting bodies. Decision reasons and every
supporting and conflicting excerpt are redacted and truncated for display, and
each decision is labeled `current`, `stale`, or `legacy` by contract. Extraction
attempts record the contract version and prompt hash of the decision they
consume, including legacy versions.

Live stage commands and `reprocess` load `.env` and preflight recognized provider
credentials before observing, preparing, or claiming work. Missing credentials
exit 2 without consuming work. Every new run records `stage=eligibility|extraction`
and `trigger=scheduled|reprocess`. Migrated legacy runs
retain `stage=combined`, with their trigger backfilled from attempt history. Audit
and per-stage usage queries live in
`scripts/pipeline_queries.sql`.

The eligibility contract is `opportunity-oneshot-v2`, embedding the
operator-approved opportunity-screening-v2 policy and six synthetic examples
written from the policy, whose text occurs in no corpus posting; extraction keeps
its independent extractor version. Any semantic change
to the policy, examples, response shape, structural rules, or grounding
normalization bumps the contract version; the prompt hash changes with the
prompt, input-wrapper, or schema bytes. Reuse, recordings, and evaluator replay
require both. Because no durable facts exist, a policy change has no free
re-screen: it requires model-backed eligibility reprocessing of every affected
job. Decisions made under the retired verdict contract (`cr-2026.10.1`) keep a
null prompt hash and are auditable but never reused or extracted.

The development evidence obligations were operator-reviewed on 2026-11-15, and the
`opportunity-oneshot-v1` contract then failed the development gates in three
five-replay `gpt-4.1-mini` evaluations (the original prompt and the two allowed
revisions; see the SYS-2028 negative result below). `opportunity-oneshot-v2` fixes
the measurement problem that result exposed, and every candidate model tried on it
has also failed the development gates (see the v2 comparison below). No
candidate is frozen, and no exposed-regression result, blind-corpus result, shadow
review, or rollout exists.

SYS-2028 negative result (development partition, final prompt hash
`a0ab9788…eeb2`): schema validity, structural consistency, and primary grounding
were 100%, primary false eligibles were 0, and all 18 primary cases were
unanimous across replays. The gates still failed: wrongful ineligible calls
(BAJ03 reads `Latin America (Mexico and Colombia preferred)` as a restriction),
KAK02 called unknown, critical evidence recall 0.92, role agreement 0.83, and
unmet manual reason review. Among the six prompt-example cases, GOL03 is
ungrounded 5 of 5 because the model emits NUL characters for the `·` separator,
OCE04 is called eligible 5 of 5, and NBS03 once. All three development unknown
cases are prompt examples, so the primary score has no unknown ground truth and
unknown behavior is measured only by the contaminated secondary score. Each
five-replay run cost about $0.15. The first revision also quoted wording from two
scored cases (BAJ03 and KAK02), contaminating them for the later runs.

`opportunity-oneshot-v2` replaces the corpus-derived examples with six synthetic
examples of the same patterns and rewrites every policy phrase that quoted a
posting, so no development case is contaminated: all 24, including the three
unknown cases, now count in the primary score, and the secondary score is empty.
`tests/unit/test_eligibility_prompt.py` fails if any multi-word example line,
excerpt, or quoted policy phrase occurs in a corpus posting. The development cases
have still been read across three evaluation rounds, so development results are
optimistic; the fresh blind corpus remains the release evidence.

SYS-2028 v2 comparison (development partition, prompt hash `3c1afd4e…`, five
replays each, scored after the KAK01 and OCE04 obligation fixes recorded in the
manifest history): no candidate passed. `gpt-4.1-mini` (about $0.15 per run)
reached macro F1 0.744 with 10 false eligibles (NBS03 and OCE04), GOL03
ungrounded 4 of 5, and KAK02 unknown. `gpt-4.1` (about $0.76) had perfect schema,
consistency, and grounding and all 24 cases unanimous, but called NBS03 eligible
and KAK02 unknown 5 of 5 (macro F1 0.867). `gpt-5.4-mini-2026-03-17` without
reasoning (about $0.30) reached macro F1 0.873 but called NBS03 eligible 5 of 5
and was unstable on BAJ02. The same model with `--reasoning-effort low` (about
$0.32) reached macro F1 0.946 with one false eligible (NBS03, 1 of 5) and decided
BAJ02 and OCE04 correctly in every replay, but its evidence broke the structural
rules: it cited one excerpt in both buckets (BAJ02 4 of 5, OCE04 1 of 5) and joined
non-contiguous lines into one excerpt (KAK01 2 of 5), giving structural
consistency 0.958 and grounding 0.942. With `--reasoning-effort medium` (about
$0.36) it decided all 120 calls correctly (macro F1 0.998, no false eligibles, no
wrongful ineligibles, every structural check passed), and failed only because one
RAI02 replay misquoted `We don't expect you` as `You don't expect you`, which
grounding rejected (grounding 0.992, critical evidence recall 0.993), and because
the five RAI02 reasons still awaited operator disposition.

Gate policy 2 (operator amendment, 2026-11-21, recorded in
`docs/ELIGIBILITY_FLOW.md` before any freeze): grounding must reach at least 99% of
calls rather than 100%, and critical evidence recall counts only grounded calls. A
misquote is rejected and retried in production, never stored. The amendment came
after seeing the near-miss above, so exposed-regression and blind results, not the
development cases, decide release. Under gate policy 2 the medium-effort candidate
passed the development gates once the operator accepted the five RAI02 reasons,
and it was frozen (`output/sys2028/v2/freeze-g54mini-medium.json`).

Exposed-regression report for that frozen candidate (8 held-out cases, five
replays, about $0.13, report only, no tuning): schema, structure, and grounding
100%, no false eligibles and no wrongful ineligibles, 38 of 40 decisions correct
(macro F1 0.921). GOL04, labeled unknown because `Remote in Latin America`
conflicts with `Work from anywhere in South America`, was called ineligible in 2
of 5 replays, so that safety case was not unanimous. Evidence agreement was 0.625
because these eight cases have no reviewed obligations and are scored against
their full `must_cite` quotes: JOB04 correctly used the missing-information form,
KAK04 cited a shorter exact prefix of the Austin sentence, and OCE03 cited the
header lines separately. The operator accepted 16 of the 18 divergent reasons and
rejected GOL04 replays 3 and 5, which let a perks line override the role header.

Blind result for the frozen candidate (`opportunity-screening-blind-v1`: 51 fresh
postings from 30 companies, 23 eligible, 11 ineligible, 17 unknown, labelled and
approved before any model call; five replays, about $1.00): **the gates failed.**
Schema validity 100%, no false eligibles, decision macro F1 0.951, critical
evidence recall 0.996, and decision-plus-evidence agreement 0.929 passed; grounding
0.965 (below 0.99), two wrongful ineligibles (BL04, a role listing several
foreign remote countries plus Latin America), and four unstable safety cases
(BL08, BL11, BL13, BL40) failed. Two labelling errors of mine (BL11, BL38 used the
missing-information form although each posting has one geographic line) were
corrected after the run with decisions unchanged, as the manifest history records.
The operator released the candidate anyway on 2026-11-21 as the eligibility
default and a starting point. `docs/ELIGIBILITY_FLOW.md` describes the current flow,
its known weaknesses, and the next steps; shadow review has not run.

### Operating discovery

The dataset answers what is open *right now*, so it is worth refreshing two or
three times a week rather than continuously. The default freshness window is 20
hours, which means a re-run on the same day costs nothing for boards that
already succeeded and retries only the ones that did not.

Expect the board count in `job_urls` to be lower than the number of companies in
the corpus. A board that stores no rows disappears from that table while still
having run successfully: the first full run covered 116 companies and 9 of them
stored nothing, leaving 1504 URLs across 107 boards. An empty result is a
successful run, not a failure, and `company_runs` is where to confirm which
reading applies.

Runtime health is observed state, not a comparison with onboarding evidence.
Inspect status, stored URL count, errors, and timestamps for the latest batch:

```bash
sqlite3 data/job-discovery.db 'select company_slug, status, url_count, error_message, started_at, finished_at from company_runs where batch_id = (select max(batch_id) from company_runs)'
```

A successful run with zero stored URLs is the line worth investigating. It may
mean the board emptied or that navigation never reached extraction; only a look
at the site tells which.

A full run of the current 116-company corpus takes about 16 minutes and peaks
around 8 GB of Chromium residency at the default ceiling of 4 concurrent
browser runs. Raising that ceiling costs proportionally more memory without
returning measurable throughput at this corpus size, so leave it alone unless
the corpus grows enough that boards genuinely queue for a slot. `docs/ARCHITECTURE.md`
records the measurements behind both ceilings.

Back up the database by copying the file. There is no export step because the
discovery snapshot and pipeline audit history share one SQLite file:

```bash
cp data/job-discovery.db "data/job-discovery-$(date +%Y%m%d).db"
```

A scan can rebuild the current discovery URL snapshot. It cannot recreate prior
pipeline content checkpoints, attempts, decisions, accepted results, or their
audit timestamps, so preserve backups when that history matters.

### Adding a new company

Companies are integrated through the tracked `new-companies.json` queue plus an
agent skill, not by editing the catalog directly. The skill reads from the queue,
measures unfiltered ground truth, runs the live integration, captures the exact
saved report path, machine-checks that report, captures a regression snapshot,
gates the quality checks, and commits the result.

`new-companies.json` is tracked in this repository;
`new-companies.example.json` documents its schema. If the queue file is missing,
restore it from git rather than creating an untracked personal copy:

```bash
git restore new-companies.json
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

The `expected_jobs` value is onboarding-only queue evidence: a nonnegative JSON
integer counted by a human. The skill keeps it in working context and compares
it externally with the saved live report, but never copies it into `Company`,
runtime reports, or the database. It is never adjusted to match observed
reality. The `aliases` list may stay empty unless the auto-derived handle is
awkward to type as `-c <handle>`.

Once at least one entry is filled in, invoke the `integrate-company` skill with
the company name or any alias. The skill handles ground-truth verification,
snapshot capture, the live agent run, the quality gates, and a single `feat:`
commit on its own branch covering `catalog/companies.py` plus the new snapshot
directory, ready to push as a pull request. On
success, it removes the entry from `new-companies.json`. That removal is a
tracked workflow change and should be staged with the integration when removing
the completed entry is the intended workflow.

## Development

```bash
uv run ruff format .                       # format
uv run ruff check . --fix                  # lint
uv run mypy                                # type-check
uv run --group test pytest tests/          # full suite
uv run --group test pytest tests/unit/     # browser-free subset
uv run pre-commit install                  # optional: git hooks
```

Changes reach `main` only through pull requests, which are squash-merged with
the PR title as the commit subject. CI (`.github/workflows/ci.yml`) runs the
same gates as the hooks, plus a check that the built wheel installs and runs,
and it must pass — along with a check that the PR title follows the commit
convention — before a pull request can merge.

Design documents live in `docs/`. `docs/ARCHITECTURE.md` holds the
big-picture map and the invariants. `CLAUDE.md` and `TABNINE.md` are guidance
files for the two AI assistants used on this repo and carry the same project
facts. `blockers/INTEGRATION_BLOCKERS.md` catalogues the site behaviours that
resist extraction, referenced as C1–C24 throughout the codebase.
