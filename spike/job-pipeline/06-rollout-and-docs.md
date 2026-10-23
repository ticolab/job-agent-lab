# Phase 6 — Rollout, model comparison, and documentation

Goal: the first real pass over the corpus, the evidence that the lifecycle
holds, a measured model choice, and the documentation that lets the next
person operate this without the design conversation. One pull request for
documentation; the runs themselves produce database state and a short
report committed under `spike/job-pipeline/reports/`.

## Task 6.1 — Migrate and preview the production database

- Objective: see the whole corpus's disposition before spending anything.
- Changes:
  - [ ] Back up `data/job-discovery.db` by file copy with a dated name.
  - [ ] `uv run alembic upgrade head`.
  - [ ] `uv run job-pipeline preview --all --by-company`; record the
        output in `reports/01-preview.md`. Expect every current URL as
        `new` and the identity collapse count to match the Phase 2
        measurement for whatever snapshot is current.
- Dependencies: Phase 5 merged.
- Verification: counts sum to the `job_urls` row count minus collapses.
- Effort: 0.5 hours.

## Task 6.2 — Staged first run

- Objective: catch a systematic problem on ten jobs rather than a thousand.
- Changes:
  - [ ] `run -c <one greenhouse board> --limit 5`, then `show` each job;
        confirm rung used, decision, evidence grounded, result sane.
  - [ ] `run -c <one workday board> -c <one icims board> --limit 10`; same
        review; note any board that needed the rendered rung.
  - [ ] `run --all --limit 100`; review the failures list; fix anything
        systematic (a fetch mapping, a closed phrase, an alias) in a PR
        before continuing.
  - [ ] `run --all` to completion, possibly across two days as retries
        come due. Record elapsed time, token totals, and cost in
        `reports/02-first-run.md` with the disposition table.
- Dependencies: 6.1.
- Verification: `select status, count(*) from jobs group by status` shows
  no `new` and no `in_progress` rows after the final run.
- Effort: 3 hours of operator time spread over the runs.

## Task 6.3 — Prove the lifecycle on the second day

- Objective: the property the whole plan exists for.
- Changes:
  - [ ] After the next discovery scan, `preview --all`; the completed
        counts carry over and only genuinely new URLs are `new`.
  - [ ] `run --all`; confirm in `reports/03-second-run.md` with two
        queries: the number of attempts whose job already had a completed
        status before the run is zero, and token usage equals the sum over
        new and retried jobs only.
  - [ ] Kill a run mid-way once, deliberately, then rerun; confirm the
        reaper released the interrupted jobs and nothing completed was
        touched. Record it.
- Dependencies: 6.2.
- Verification: the two queries, pasted into the report.
- Effort: 1.5 hours.

## Task 6.4 — Audit queries

- Objective: the questions REQ-OPS-7 asks, answerable in SQL and kept in
  the repo.
- Files: `scripts/pipeline_queries.sql`.
- Changes:
  - [ ] Dispositions per company; attempts per job above one; jobs with
        `failed` fields in their current result; evidence grounding
        re-check (every quote in `job_decisions.evidence_json` and
        `job_results.result_json` is a substring of its content's text,
        done in Python because SQLite has no normalization function, as
        `scripts/verify_grounding.py`); cost per run and per outcome;
        unknown decisions with their reasons for policy review.
- Dependencies: 6.2.
- Verification: `verify_grounding.py` reports zero misses on the first-run
  database.
- Effort: 2 hours.

## Task 6.5 — Model comparison

- Objective: a measured choice rather than a default.
- Changes:
  - [ ] Run `evaluate_leaf.py` for both leaves on the default OpenAI model
        and on `claude-sonnet-5-5` and `claude-haiku-5-5` through LiteLLM,
        with `--price-per-million` taken from each provider's pricing page
        on the day of the run.
  - [ ] Record per-component scores, grounding failures, latency, and cost
        per posting in `reports/04-model-comparison.md`.
  - [ ] If a different model wins on quality at acceptable cost, change
        `DEFAULT_MODEL` in `job_pipeline.settings` in its own PR; bump
        neither leaf version, since the prompt did not change.
- Dependencies: Phases 3 and 4 evaluation scripts.
- Verification: the report; thresholds from Phases 3 and 4 met by the
  chosen model.
- Effort: 3 hours.

## Task 6.6 — Documentation

- Objective: the four guidance files describe the system as built.
- Files: `ARCHITECTURE.md`, `CLAUDE.md`, `TABNINE.md`, `README.md`,
  `JOB_EXTRACTOR_REQUIREMENTS.md`, `prompts/`.
- Changes:
  - [ ] `ARCHITECTURE.md`: a section on the three packages, the
        dependency rules in the four-root form, the single-Base decision,
        the lifecycle table, the identity rule and its measured basis, why
        the leaves are pure, why two calls, why RESTRICT, why unknown is
        terminal, and what v1 deliberately leaves out (D15).
  - [ ] `CLAUDE.md`: commands for `job-pipeline`, the layering table with
        four roots, the "each repository call gets its own session" rule
        restated for the pipeline, the golden corpus and evaluation
        workflow, the version-bump rule for prompts, and the instruction
        that technology normalization lives in code.
  - [ ] `TABNINE.md`: the same project facts, kept consistent per the
        existing convention.
  - [ ] `README.md`: the daily workflow in five commands.
  - [ ] `JOB_EXTRACTOR_REQUIREMENTS.md`: add a status line pointing to this
        directory and mark the open decisions resolved with the decision
        log numbers. Rename nothing else.
  - [ ] `prompts/`: add `README.md` stating the four files are historical
        evidence superseded by the two leaves, and commit them, since they
        are currently untracked.
- Dependencies: 6.2.
- Verification: `tests/unit/test_skill_sync.py` still green; a reader can
  run the daily workflow from `README.md` alone.
- Effort: 4 hours.

## Task 6.7 — Follow-ups recorded, not built

- Objective: the deliberate omissions have a home.
- Files: `spike/job-pipeline/07-followups.md`.
- Changes:
  - [ ] `recheck` command: re-fetch completed jobs on a slow cadence to
        detect URL reuse and closed postings.
  - [ ] Cross-company duplicate detection by fingerprint across slugs.
  - [ ] Structured compensation parsing.
  - [ ] A dashboard read model, once the consumer exists.
  - [ ] Bulk reprocess by version change, if a policy change ever warrants it.
- Dependencies: none.
- Effort: 0.5 hours.

## Testing strategy

| Test type | Scope | Priority |
|---|---|---|
| Manual | staged runs, second-day run, kill-and-recover | P0 |
| Script | `verify_grounding.py` zero misses | P0 |
| Manual | model comparison with thresholds | P1 |
| Unit | skill sync and docs references still valid | P1 |

## Done

- [ ] Reports 01 to 04 committed under `spike/job-pipeline/reports/`.
- [ ] PR `docs: describe the job pipeline packages, lifecycle, and daily workflow`.
- [ ] Overview definition-of-done checklist fully ticked.
