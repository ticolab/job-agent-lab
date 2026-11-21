# Eligibility Flow: Current State (2026-11-21)

This document describes how job-posting eligibility works today, what the
evaluation showed, and what remains to be done. "Eligible" means *retain the
posting for follow-up* by one candidate who stays based in Costa Rica. It is not
a judgment on payroll, work authorization, visa, schedule, skill fit, or vacancy.

The project is pre-production and runs on one machine. Its data is disposable, so
a design is replaced outright instead of kept compatible: there are no version
tags, legacy columns, or upgrade paths.

## Status at a glance

| Item | State |
|---|---|
| Eligibility model (default) | `gpt-5.4-mini-2026-03-17`, reasoning effort `medium` |
| Extraction model (default) | `gpt-4.1-mini`, no reasoning effort (never compared) |
| Prompt fingerprint | `3c1afd4e…3b3da` (automatic, see below) |
| Development gates | Passed (24 postings × 5 replays) |
| Held-out check | Reported, no unsafe errors (8 postings × 5 replays) |
| Blind test | **Not passed** (51 postings × 5 replays); used as a starting point |
| Shadow review on live postings | Not run |

## How the flow works

1. **Discovery** (`job-discovery scan`) stores the current posting URLs per company.
2. **Eligibility** (`job-pipeline eligibility`) turns new URLs into jobs, fetches
   each posting (HTTP first, browser rendering as fallback), and makes **one
   model call** per posting.
3. The model returns exactly four fields: `decision` (`eligible`, `ineligible`,
   `unknown`), a one-sentence `reason`, `supporting_evidence`, and
   `conflicting_evidence`.
4. Python then checks the answer, and never re-derives the geography itself:
   - strict schema;
   - structure: only the fixed "states nothing about location" answer may cite
     nothing, and no excerpt may appear in both lists (a repeat inside one list
     is merged);
   - grounding: each excerpt's **words** must occur in the posting as one
     unbroken run of whole words in the same order. Case, punctuation, symbols,
     and whitespace are ignored. Paraphrases, reordered text, partial words, and
     text stitched from separate passages are rejected.
5. An accepted answer is stored atomically. The stored evidence is the posting's
   own text for the matched span, not the model's copy. The job then moves on:
   - `eligible` → `ready_for_extraction`;
   - `ineligible` → `completed_ineligible`;
   - `unknown` → `unknown` (terminal until reprocessed).
6. A rejected answer is **never stored**. The job is retried after one day,
   then two days, and becomes `failed` after three attempts.
7. **Extraction** (`job-pipeline extraction`) runs on jobs whose current decision
   is eligible on the same stored text.

### The prompt fingerprint

Nothing is versioned by hand. `ELIGIBILITY_PROMPT_HASH` is computed from the
prompt, the input wrapper, and the response schema. Each decision stores it, a
stored decision is reused only under the same fingerprint, and saved evaluation
answers are tied to it, so one prompt's answers are never scored as another's.
Changing the prompt, the grounding rules, or the model never blocks the pipeline
and never re-decides jobs on its own.

### The policy in brief

- **Eligible:** Costa Rica, LATAM, the Americas, worldwide, or plain remote
  work, with nothing excluding or conflicting with a Costa Rica-based candidate.
- **Ineligible:** explicit wording that excludes that candidate. Examples: must
  live or be based elsewhere, authorization required for another country, a
  required foreign work location, or recurring on-site or hybrid attendance
  abroad.
- **Unknown:** role statements conflict (for example a city header against a
  regional permission), or the posting does not establish the geography. A
  foreign place that appears only in a title or label, a time zone alone, and
  ambiguous names ("San Jose", two-letter codes) all give unknown.
- The prompt has six synthetic examples. None of their text occurs in any
  evaluation posting, and a test enforces that.

## Evaluation gates

A candidate is evaluated with five replays per posting, in three stages: a
development set it may be tuned on, a held-out set read only after the
candidate is frozen, and a blind set. Automated gates:

- 100% schema-valid and structurally consistent answers;
- grounding of at least 99% of answers;
- 100% recall of the reviewed critical evidence, measured over accepted answers;
- decision macro F1 of at least 0.90, and decision-plus-evidence agreement of
  at least 0.90;
- zero false eligibles and zero wrongful ineligibles;
- every safety-critical case unanimous across replays, and at least 90% of all
  cases unanimous;
- every divergent reason reviewed by the operator.

The blind set must hold at least 10 postings of each label, labelled before any
model call. The held-out and blind runs accept only a frozen candidate: the
freeze record names the model, effort, and prompt fingerprint.

## Evidence

| | Development (24) | Held-out (8) | **Blind (51)** |
|---|---|---|---|
| False eligible | 0 | 0 | **0** |
| Wrongful ineligible | 0 | 0 | **2** |
| Decision macro F1 | 0.998 | 0.921 | 0.968 |
| Grounding (gate ≥ 0.99) | 0.992 | 1.000 | 0.992 |
| Critical evidence recall | 1.000 | n/a¹ | 0.996 |
| Decision + evidence agreement | 0.992 | n/a¹ | 0.957 |
| Cases unanimous across replays | 23/24 | 7/8 | 46/51 |
| Cost per run | $0.36 | $0.13 | $1.00 |

¹ The held-out cases have no reviewed evidence lists, so their evidence scores
are not comparable.

Notes:

- **What still fails on the blind set.** Two wrongful ineligibles (BL04), one
  answer missing its critical evidence (BL38), three safety-critical cases that
  are not unanimous (BL08, BL11, BL13), and 39 reasons awaiting operator review.
- **Word-level grounding was added after the blind run.** Under the earlier
  exact-text rule the blind grounding was 0.965: six of nine rejections were a
  period the model added to a sentence it cut short. The same saved answers were
  re-scored under the word rule, at no cost. Because the rule was designed from
  those results, its blind numbers are optimistic until new postings confirm them.
- **Cost and speed.** About $0.004 and 2 seconds per posting at medium effort,
  with one call per posting in production.
- **Blind labels.** Claude proposed the blind labels from the policy text, the
  operator approved them before any model call, and no GPT model saw the
  postings. Two labelling errors (BL11 and BL38) were corrected after the run
  with their decisions unchanged. The corpus manifest records the correction.
- **Other candidates.** `gpt-4.1-mini`, `gpt-4.1`, and `gpt-5.4-mini` with no
  reasoning or with low effort all failed development.

## Where the labels come from

The 32 postings in `opportunity-screening-v2` were collected on 2026-11-01 from
eight companies and labelled by the operator. Each case folder holds the posting
text, the label, the operator's rationale, and the required quotes.
Case-specific judgments to keep in mind:

- OCE03 is unknown: a foreign header alone is not a restriction.
- GOL04 stays unknown: no precedence of "Latin America" over "South America" was
  approved.
- KAK01 and KAK02 are resolved for *opportunity screening*, not legal eligibility.
- JOB03 and JOB04 stay unknown rather than inheriting context from the job board.

## Known weaknesses

1. **Real decision mistakes (about 3% of blind answers, none a false eligible).**
   - BL04: the location line reads "Remote in Chile / Remote in Philippines /
     Remote in Latin America / Remote in Argentina". The model called it
     ineligible 2 of 5 times and unknown 3 of 5, although Latin America includes
     Costa Rica. Details and a proposed rule: `ISSUE_BL04_LATAM_COUNTRY_LIST.md`.
   - Borderline cases occasionally flip: a bare US region label (BL11), and on
     the held-out set a perks line ("anywhere in South America") overriding the
     role header (GOL04).
2. **Answers that are still rejected (under 1%).** The same excerpt cited in
   both lists (BL08, BL13), and a quote whose words were changed ("We don't" →
   "You don't"). These cost retries, not wrong decisions.
3. **Fetching loses far more postings than the model does.** In a sample of 164
   postings from 50 companies, about 12% could not be fetched (off-site
   redirects, blocks, timeouts, and not-found pages), and about 14% returned a
   page that was not a real posting (a block page or "this job has been
   filled"). Details and fixes: `ISSUE_FETCHING_LOSSES.md`.
4. **Policy edges that may surprise.**
   - "Remote (PST/MST)" is eligible, because a time zone is a schedule.
   - A foreign city that appears only in a title is unknown, not ineligible.
   - Expect more `unknown` results than a human screener would give.

## Operating it

```bash
uv run alembic upgrade head                         # create the database (one revision)
uv run job-pipeline eligibility --all               # gpt-5.4-mini-2026-03-17, medium effort
uv run job-pipeline eligibility --all -m gpt-4.1-mini   # explicit model, no default effort
uv run job-pipeline extraction --all                # gpt-4.1-mini
uv run job-pipeline reprocess --job 42 --from-phase eligibility --reason "re-decide"
uv run job-pipeline show --job 42                   # decision, evidence, model, effort
```

- **Schema changes.** The schema is one squashed revision. After a model change,
  regenerate it and recreate the database; there is no upgrade path.
- **What each row records.** Every run and attempt stores the model and its
  `reasoning_effort` (null means `temperature=0`). Decisions store the prompt
  fingerprint.
- **Re-deciding.** Changing the model or prompt does not re-decide existing
  jobs. Use `reprocess --from-phase eligibility` for that.
- **Unknown jobs.** `unknown` is terminal. Review them with `show` and reprocess
  them when the policy or model improves.

## Next steps

1. **Fetching.** Recover the postings lost to off-site redirects and blocks, and
   stop "job filled" and block pages from reaching the model as postings.
2. **Prompt change for BL04.** Add an explicit rule and a synthetic example for
   a list of foreign places that includes a regional permission, then re-run
   the evaluations (about $1.50) on a new blind set.
3. **Immediate retry.** Retry a rejected answer in the same run, telling the
   model which excerpt was not found, instead of waiting a day.
4. **Shadow replay.** Run `scripts/replay_eligibility.py` (read-only) over
   stored postings. It needs `--reasoning-effort` first.
5. **Extraction model.** `gpt-4.1-mini` has never been compared against another
   model.
6. **Operator review.** The 39 divergent blind reasons are still pending.

## Where things live

| What | Where |
|---|---|
| Prompt, schema, grounding | `src/job_eligibility/` |
| Pipeline, persistence, CLI | `src/job_pipeline/` |
| Defaults | `src/job_pipeline/settings.py` (`ELIGIBILITY_MODEL`, `ELIGIBILITY_REASONING_EFFORT`, `DEFAULT_MODEL`) |
| The policy text the model sees | `SYSTEM_PROMPT` in `src/job_eligibility/prompt.py` |
| Labels, rationale, and quotes per posting | `expected_eligibility.json` and `expected_evidence.json` in each corpus case folder |
| Development and held-out corpus | `tests/fixtures/postings/opportunity-screening-v2/` |
| Blind corpus | `tests/fixtures/postings/opportunity-screening-blind-v1/` |
| Evaluator | `scripts/evaluate_leaf.py` (`--reasoning-effort`, `--partition blind`) |
| Run reports, recordings, freeze record | `output/` (local and gitignored, not in the repository) |
