# Job Pipeline — Implementation Plan Overview

Status: approved for implementation. Decisions below were settled in the
design conversation of 2026-10-23 and supersede the open items in
`JOB_EXTRACTOR_REQUIREMENTS.md`. That document remains the statement of
goals; this directory is the statement of how.

## What is being built

Three new packages over the existing `job_discovery` kernel. They read the
current posting URLs discovery produces, decide once per logical job whether
the posting is open to someone working from Costa Rica, fully extract the
eligible ones, and keep a durable history so a job is never paid for twice.

```
src/
  job_discovery/      unchanged in behaviour; never imports the new packages
  job_eligibility/    text in, eligibility decision out    (pure, no I/O)
  job_extractor/      text in, structured job result out    (pure, no I/O)
  job_pipeline/       identity, content, tables, lifecycle, model client, CLI
```

The two leaves are libraries. They render a prompt, declare a response
schema, and validate a model response against the posting text. They do not
call the model, open a browser, or touch the database. `job_pipeline` owns
every side effect and composes the leaves, exactly as `job_discovery.batch`
composes strategies that cannot see persistence.

## Decision log

| # | Decision | Chosen |
|---|---|---|
| D1 | Package shape | Three sibling packages, one console script `job-pipeline`. The leaves import nothing from the repo. |
| D2 | Calls per new job | Two: a small eligibility call, then an extraction call only when eligible. Both read the same stored text. |
| D3 | Model provider | LiteLLM with structured outputs. Default `gpt-4.1-mini`, overridable with `-m`. `OPENAI_API_KEY` from `.env`. Claude compared on the golden set in Phase 6. |
| D4 | "Remote" with no region | Unknown. |
| D5 | Preference language ("ideally based in Mexico") | Eligible. Only an explicit permitted list that omits Costa Rica excludes. |
| D6 | Vocabularies | Legacy sets for work mode, employment type, experience level, job function, with "not stated" added and the overlap between Technology & Engineering and Data & Analytics fixed. Multiple job functions allowed. |
| D7 | Golden set | Built before production prompts: ~30 postings across ATS types. Expected outputs drafted by the assistant, reviewed and corrected by the operator. |
| D8 | Retry policy | Unknown is terminal. 404, 410, or an explicit closed page is terminal. Timeouts, 5xx, blocks, empty content, model errors, validation failures retry up to 3 times with linear daily backoff, then terminal. |
| D9 | Technology normalization | In code, from an alias table seeded by `prompts/stage_4_tech.md`. Versions dropped. Distinct products never merged. |
| D10 | Browser fallback | Playwright through `job_discovery.settings.launch_capture_browser`, no agent. |
| D11 | Main-content extraction | `trafilatura`. |
| D12 | Source language | Source wording kept in its language; normalized values and the summary in English. |
| D13 | Plan location | `spike/job-pipeline/`, committed. |
| D14 | Database | Same SQLite file, new tables sharing `job_discovery.persistence.models.Base`, one Alembic chain. |
| D15 | URL reuse detection and cross-company duplicates | Out of v1. Both would require re-fetching completed jobs. Noted for a later `recheck` command. |

## Dependency rules

Enforced by `tests/unit/test_layering.py`, which Phase 1 generalizes from
one package root to a table of roots.

```
job_pipeline    -> job_eligibility, job_extractor,
                   job_discovery.domain, job_discovery.catalog,
                   job_discovery.persistence, job_discovery.settings
job_eligibility -> (nothing in the repo)
job_extractor   -> (nothing in the repo)
job_discovery   -> (its existing table; never any new package)
```

Inside `job_pipeline` the inner rows mirror discovery:

```
cli         -> everything in job_pipeline, plus the externals above
runner      -> policy, identity, content, llm, persistence, leaves
content     -> job_discovery.settings (browser launch), nothing else internal
identity    -> nothing internal
persistence -> job_discovery.persistence (Base, engine, timestamps)
llm         -> nothing internal
```

## Data model

Seven tables, each answering one question. All timestamps naive UTC through
`job_discovery.persistence.timestamps`. Every statement lives in a
repository module under `job_pipeline/persistence/`.

| Table | Question it answers | Key rows |
|---|---|---|
| `jobs` | What logical jobs exist and where does each stand | `id`, `company_slug` FK RESTRICT, `url_key` (unique with slug), `status`, `next_phase`, `attempt_count`, `next_attempt_after`, `merged_into_id`, `first_seen_at`, `last_seen_at`, `updated_at` |
| `job_sources` | Which raw URLs were observed for a job | `job_id`, `url` (unique with job), `first_seen_at`, `last_seen_at` |
| `job_contents` | What exact text was evaluated | `job_id`, `content_hash` (unique with job), `fingerprint`, `requisition_id`, `resolved_url`, `http_status`, `method`, `title`, `text`, `jsonld_json`, `retrieved_at` |
| `job_attempts` | What was tried, when, at what cost | `job_id`, `run_id`, `trigger`, `reason`, `phase_reached`, `outcome`, `error_category`, `error_message`, `model`, `prompt_tokens`, `completion_tokens`, `policy_version`, `extractor_version`, `started_at`, `finished_at` |
| `job_decisions` | What eligibility was decided and on what evidence | `job_id`, `attempt_id`, `content_id`, `decision`, `reason`, `evidence_json`, `policy_version`, `is_current`, `decided_at` |
| `job_results` | What was extracted and accepted | `job_id`, `attempt_id`, `content_id`, `extractor_version`, `schema_version`, `result_json`, `is_current`, `accepted_at` |
| `pipeline_runs` | What one run did | `id` (sortable, like `batch_id`), `started_at`, `finished_at`, `selection`, `counts_json`, `model` |

`companies` is referenced with `ON DELETE RESTRICT`, not cascade: the whole
point of this history is to outlive the discovery snapshot, so a catalog
edit must never be able to delete it.

## Job lifecycle

```
status                 meaning                                    ordinary run
---------------------  -----------------------------------------  ------------
new                    observed, never attempted                  process
in_progress            claimed by a running pipeline              leave alone
retry                  retryable failure, next_attempt_after set  process when due
completed_eligible     decision eligible, result accepted         skip
completed_ineligible   decision ineligible                        skip
unknown                decision unknown                           skip
unavailable            not an obtainable active posting           skip
failed                 retries exhausted or permanent failure     skip
merged                 proven duplicate of another job            skip
```

`next_phase` is `fetch`, `eligibility`, or `extraction` and tells the worker
where to resume. A job whose eligibility is decided but whose extraction
failed sits at `retry` with `next_phase = extraction`, so the retry reuses
the stored content and the stored decision.

A job is claimed with a single conditional update, `status in ('new','retry')
-> 'in_progress'`, and only the caller whose update touched one row proceeds.
That is the whole answer to overlapping runs.

The work set of an ordinary run is: jobs in `new` or due `retry` whose
`url_key` is present in today's `job_urls` snapshot. A job that left the
snapshot is owed nothing. Reprocessing bypasses both the status filter and
the snapshot filter by design.

## Identity

`url_key = lowercase scheme and host, path without trailing slash, query
minus denylisted parameters, remaining parameters sorted, no fragment`.

The denylist is the 17 parameter names observed as non-identifying in the
2026-10-23 snapshot plus any `utm_*` prefix. Measured against that snapshot
the rule produces 1,503 distinct keys from 1,504 URLs with one correct merge;
path-only would merge 10 groups of distinct jobs. A fixture of those URLs
pins the rule.

Two fallbacks run after a fetch, before any model call, for a job that looks
new: the JSON-LD `identifier` when present, then a fingerprint of normalized
title plus the first 2,000 characters of text. A match marks the new row
`merged`, records its URL under the existing job, and costs one fetch.

## Versions

Each leaf exports a human-readable `POLICY_VERSION` or `EXTRACTOR_VERSION`
and a test asserts the version string changes whenever the prompt text or
schema hash changes. A version change never triggers automatic reprocessing.

## Phases

| File | Phase | Delivers |
|---|---|---|
| `01-foundations.md` | 1 | Packages, tooling, tables, migration, repositories, layering |
| `02-identity-and-content.md` | 2 | URL identity, content ladder, capture script, golden corpus text |
| `03-eligibility.md` | 3 | `job_eligibility` leaf, model client, golden expectations, eval script |
| `04-extractor.md` | 4 | `job_extractor` leaf, normalization, golden expectations |
| `05-pipeline-and-cli.md` | 5 | Observe, plan, worker, scheduler, retry policy, CLI |
| `06-rollout-and-docs.md` | 6 | First live runs, model comparison, docs, cost record |

Phases 3 and 4 depend on 2. Phase 5 depends on 1, 3, and 4. Each phase ends
in one or more pull requests that pass `ci-ok` on their own, so the
repository is always releasable.

## Definition of done for the whole plan

- [ ] `job-pipeline preview --all` on the production database reports the
      disposition of every current URL without network or model calls.
- [ ] `job-pipeline run --all` completes the first pass over the corpus; every
      job has a terminal or retry status and an attempt row.
- [ ] A second `run --all` the next day makes no model call for any job the
      first run completed, verified from `job_attempts`.
- [ ] Eligibility and extraction golden evaluations meet the thresholds set in
      Phase 3 and 4 on the chosen model.
- [ ] Every evidence quote in accepted decisions and results is a substring of
      the stored content, verified by a query in Phase 6.
- [ ] `ARCHITECTURE.md`, `CLAUDE.md`, `TABNINE.md`, and `README.md` describe the
      three packages, the dependency rules, and the lifecycle.
- [ ] The full suite passes without `OPENAI_API_KEY`; CI stays secret-free.
