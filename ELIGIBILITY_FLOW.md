# Eligibility Flow: Current State (2026-11-21)

This document describes how job-posting eligibility works today, what the
evaluation showed, what was released and why, and what remains to be done.
"Eligible" means *retain the posting for follow-up* by one candidate who stays
based in Costa Rica. It is not a judgment on payroll, work authorization, visa,
schedule, skill fit, or vacancy (see `docs/GEOGRAPHIC_ELIGIBILITY_REVIEW.md`).

## Status at a glance

| Item | State |
|---|---|
| Contract | `opportunity-oneshot-v2`, prompt hash `3c1afd4e…3b3da` |
| Eligibility model (default) | `gpt-5.4-mini-2026-03-17`, reasoning effort `medium` |
| Extraction model (default) | `gpt-4.1-mini`, no reasoning effort (unchanged, never compared) |
| Development gates | Passed under gate policy 2 (24 cases × 5 replays) |
| Held-out regression check | Reported, no unsafe errors (8 cases × 5 replays) |
| Blind release test | **Failed** (51 fresh postings × 5 replays) |
| Release decision | Released anyway by the operator on 2026-11-21 as a **starting point** |
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
     nothing, and no excerpt may appear twice or in both lists;
   - grounding: every excerpt must appear in the posting after normalizing case,
     whitespace, dashes, and layout separators. Paraphrases, reordered or
     joined text are rejected.
5. An accepted answer is stored atomically with the contract version and
   prompt hash, and the job moves on:
   - `eligible` → `ready_for_extraction`;
   - `ineligible` → `completed_ineligible`;
   - `unknown` → `unknown` (terminal until reprocessed).
6. A rejected answer is **never stored**. The job is retried after one day,
   then two days, and becomes `failed` after three attempts.
7. **Extraction** (`job-pipeline extraction`) runs only on jobs whose current
   eligible decision was made under the current contract on the same stored
   text. Older decisions (for example the 10 local `ready_for_extraction` jobs
   decided under `cr-2026.10.1`) fail with `missing_checkpoint` until they are
   reprocessed from eligibility.

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

## Evidence

All runs used five replays per posting. **Gate policy 2** was an operator
amendment recorded before the freeze: grounding must reach at least 99% of
calls instead of 100%, and critical evidence recall counts only accepted
answers. The amendment was made after seeing a development near-miss.

| | Development (24) | Held-out (8) | **Blind (51)** |
|---|---|---|---|
| False eligible | 0 | 0 | **0** |
| Wrongful ineligible | 0 | 0 | **2** |
| Decision macro F1 | 0.998 | 0.921 | 0.951 |
| Grounding (gate ≥ 0.99) | 0.992 | 1.000 | **0.965** |
| Critical evidence recall | 1.000 | n/a¹ | 0.996 |
| Decision + evidence agreement | 0.992 | n/a¹ | 0.929 |
| Cases unanimous across replays | 23/24 | 7/8 | 44/51 |
| Cost per run | $0.36 | $0.13 | $1.00 |

¹ The held-out cases have no reviewed evidence lists, so their evidence scores
are not comparable.

Notes:

- **Cost and speed.** About $0.004 and 2 seconds per posting at medium effort,
  with one call per posting in production.
- **Blind labels.** Claude proposed the blind labels from the policy text, the
  operator approved them before any model call, and no GPT model saw the
  postings. Two labelling errors (BL11 and BL38) were corrected after the run
  with their decisions unchanged. The corpus manifest records the correction.
- **Earlier candidates.** `gpt-4.1-mini`, `gpt-4.1`, and `gpt-5.4-mini` with no
  reasoning or with low effort all failed development (details in `README.md`).

## Known weaknesses

1. **Quote copying (about 3.5% of answers).** The model sometimes joins
   separate lines, drops a list dash, or rewords a phrase ("We don't" → "You
   don't"). Grounding rejects these, so they cost retries rather than wrong
   decisions. On a few postings the rate is high (3 of 5 on BL30 and BL40), so
   such a job can end `failed` after three attempts.
2. **Several foreign countries plus Latin America.** On BL04 the location line
   reads "Remote in Chile / Remote in Philippines / Remote in Latin America /
   Remote in Argentina". The model called it ineligible 2 of 5 times and unknown
   3 of 5, although Latin America includes Costa Rica. These are the two
   wrongful ineligibles.
3. **Unstable borderline cases.** A header place against a LATAM permission
   (BL08), a bare US region label (BL11), and cases where the evidence is
   cited in both lists (BL13) occasionally flip or break structure. On the
   held-out set, GOL04 let a perks line ("anywhere in South America") override
   the role header twice.
4. **Policy edges that may surprise.**
   - "Remote (PST/MST)" is eligible, because a time zone is a schedule.
   - A foreign city that appears only in a title is unknown, not ineligible.
   - Expect more `unknown` results than a human screener would give.

None of these produced a false eligible in 415 evaluation calls. The errors
point toward dropping or holding postings, not toward retaining excluded ones.

## Operating it

```bash
uv run alembic upgrade head                         # migration c7e1d4b9a2f6 records the effort
uv run job-pipeline eligibility --all               # gpt-5.4-mini-2026-03-17, medium effort
uv run job-pipeline eligibility --all -m gpt-4.1-mini   # explicit model, no default effort
uv run job-pipeline extraction --all                # gpt-4.1-mini
uv run job-pipeline reprocess --job 42 --from-phase eligibility --reason "re-decide under v2"
uv run job-pipeline show --job 42                   # decision, evidence, contract, model, effort
```

- **What each row records.** Every run and attempt stores the model and its
  `reasoning_effort` (null means `temperature=0`). Decisions store the contract
  version and prompt hash.
- **Reuse.** A stored decision is reused only for the same text, contract
  version, and prompt hash. Changing the model does not re-decide existing jobs.
  Use `reprocess --from-phase eligibility` for that.
- **Unknown jobs.** `unknown` is terminal. Review them with `show` and reprocess
  them when the policy or model improves.

## Next steps

1. **Shadow replay.** Run `scripts/replay_eligibility.py` (read-only) over
   stored postings and compare against current decisions. It needs a small
   change to accept `--reasoning-effort` before it can replay the released
   candidate.
2. **Prompt v3.** Two targets:
   - quote copying: one excerpt per list, copied character for character;
   - an explicit rule for a list of foreign places that includes a regional
     permission.

   This requires a new contract version, model-backed reprocessing, and a
   **new** blind set, because the current one has now been seen.
3. **Watch production.** Track the share of `failed` jobs from rejected answers
   and the share of `unknown` results. If retries pile up, consider one
   immediate in-run retry instead of a next-day retry.
4. **Extraction model.** `gpt-4.1-mini` has never been compared against another
   model. Evaluate it separately.
5. **Operator review.** The 39 divergent blind reasons are still pending.

## Where things live

| What | Where |
|---|---|
| Prompt, schema, grounding | `src/job_eligibility/` |
| Pipeline, persistence, CLI | `src/job_pipeline/` |
| Defaults | `src/job_pipeline/settings.py` (`ELIGIBILITY_MODEL`, `ELIGIBILITY_REASONING_EFFORT`, `DEFAULT_MODEL`) |
| Policy review | `docs/GEOGRAPHIC_ELIGIBILITY_REVIEW.md` |
| Plan, gates, and gate amendment | `docs/SYS_2028_PLAN.md` |
| Development and held-out corpus | `tests/fixtures/postings/opportunity-screening-v2/` |
| Blind corpus | `tests/fixtures/postings/opportunity-screening-blind-v1/` |
| Evaluator | `scripts/evaluate_leaf.py` (`--reasoning-effort`, `--partition blind`) |
| Run reports, recordings, freeze record | `output/` (local and gitignored, not in the repository) |
