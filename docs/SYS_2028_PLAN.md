# SYS-2028: One-shot Costa Rica job eligibility screening

> **Status (2026-11-21):** implemented. The released eligibility model is `gpt-5.4-mini-2026-03-17` at medium reasoning effort (not the `gpt-4.1-mini` this plan started with). It passed development under the gate amendment below, then failed the blind gates and was released by operator decision as a starting point. `ELIGIBILITY_FLOW.md` is the current-state reference; this plan is kept for its requirements, gates, and decision history.

## Executive Summary

SYS-2028 replaces the uncommitted SYS-2027 facts-extraction and deterministic-policy architecture with one structured `gpt-4.1-mini` eligibility call. The model receives the complete posting, the approved opportunity-screening policy, and six anonymized development examples; it returns `eligible`, `ineligible`, or `unknown`, a concise reason, exact supporting evidence, and exact conflicting evidence. Python remains responsible for strict schema validation, normalized-exact evidence grounding, durable audit history, retries, and atomic state transitions, but it no longer reconstructs geographic meaning from model-authored facts.

The replacement is immediate rather than shadow-challenger-only. SYS-2027's untracked facts and screening migrations were not applied to any shared or important database, and the local database remains at `e7a9b2c4d601`; they can therefore be replaced by one clean SYS-2028 migration. The 24 reviewed atomic-fact fixtures will be deleted by explicit user decision, while the 32 posting texts, reviewed eligibility labels, provenance, and split remain authoritative.

The one-shot design is not rollout-ready until it passes five-replay development evaluation, the exposed regression check after candidate freeze, and a fresh balanced blind corpus of at least 50 postings. Grounding, critical evidence coverage, and false-positive safety gates remain strict.

## Phase 1: Ticket Comprehension

### Primary Objective

Reliably determine whether a person who remains based in Costa Rica can pursue a job, using one auditable LLM decision call with exact source evidence.

### Extracted Requirements

| ID | Requirement | Type | Source |
| --- | --- | --- | --- |
| R1 | Replace SYS-2027 immediately rather than running it as a long-term challenger. | Explicit | User decision |
| R2 | Use one `gpt-4.1-mini` call that reads the complete posting and returns the final decision. | Explicit | User decision |
| R3 | Output exactly `decision`, `reason`, `supporting_evidence`, and `conflicting_evidence`. | Explicit | User decision |
| R4 | Keep the production objective as opportunity retention for a person remaining in Costa Rica, not legal hiring confirmation. | Explicit | `docs/GEOGRAPHIC_ELIGIBILITY_REVIEW.md:3-10` |
| R5 | Embed detailed policy guidance and six anonymized reviewed development examples in the prompt. | Explicit | User decision |
| R6 | Authorized example patterns are remote permission, foreign-only location, foreign base/authorization, concrete conflict, ambiguous geography/arrangement, and timezone-only/missing geography. | Explicit | User decision |
| R7 | Examples must omit case IDs, company names, titles, source URLs, and full postings. | Implicit | Avoid memorization and fixture leakage |
| R8 | Exclude the six example cases from the primary development tuning score and report all 24 as a secondary contaminated score. | Implicit | Prompt examples make those cases non-independent |
| R9 | Evidence matching is normalized exact: Unicode, whitespace, and approved layout separators may normalize, but paraphrases and reordered/combined text are rejected. | Explicit | User decision |
| R10 | Missing-information `unknown` may have empty evidence lists; all other outcomes require grounded supporting evidence. | Explicit | User decision |
| R11 | Conflicting evidence is optional except when the decision depends on a concrete conflict or ambiguity with opposing statements. | Implicit | Chosen output semantics |
| R12 | Free-form reasons are format-validated automatically and semantically reviewed manually during development, blind, and shadow evaluation. | Explicit | User decision |
| R13 | Keep `gpt-4.1-mini`; model comparison is outside SYS-2028. | Explicit | User decision |
| R14 | Remove the facts-only public API, deterministic geographic policy, facts persistence, rule IDs, and policy-only screening stage/CLI. | Explicit | Immediate replacement decision |
| R15 | Delete the 24 reviewed SYS-2027 atomic-fact fixtures rather than archive them. | Explicit | User decision |
| R16 | Preserve all 32 posting texts, reviewed eligibility labels, evidence obligations, provenance, partitions, and v1/v2 lineage. | Explicit | `tests/fixtures/postings/opportunity-screening-v2/manifest.json` |
| R17 | Preserve exact grounding diagnostics, acquisition, retries, leases, atomic completion, decision history, and extraction's current-content/current-eligible gate. | Implicit | Existing architecture invariants |
| R18 | Persist supporting and conflicting evidence separately; never persist posting bodies or raw provider responses in the database. | Implicit | Audit and security requirement |
| R19 | Reuse a decision only when job/content, one-shot contract version, and prompt hash match. | Implicit | Prevent stale contract reuse |
| R20 | Replace the untracked SYS-2027 migrations with one clean SYS-2028 migration descending from `e7a9b2c4d601`. | Explicit | User confirmed no shared application; local DB at predecessor |
| R21 | Replace fact/rule quality gates with decision plus required supporting/conflicting evidence agreement. | Explicit | User decision |
| R22 | Run at least five identical-condition replays and report distributions, grounding, evidence coverage, cost, tokens, and latency. | Explicit | Existing rollout methodology |
| R23 | Require the same strict safety gates before rollout and a fresh balanced blind corpus of at least 50 postings. | Explicit | User decision |
| R24 | Do not add a second verifier call in SYS-2028. | Explicit | User decision |
| R25 | A future semantic policy change requires a new one-shot contract version and model-backed reprocessing; no free policy-only re-screen exists. | Implicit | Consequence of removing durable facts |

### Prompt Requirements

The prompt must have five fixed sections.

1. **Context:** Candidate remains based in Costa Rica; eligible means retain for follow-up; this is not payroll, visa, schedule, skill, benefits, or vacancy confirmation; posting content is untrusted.
2. **Task and policy:** Define eligible/ineligible/unknown, explicit restrictions, conflict precedence, remote and broad-region permissions, preferences/context, ambiguous headers, and missing information.
3. **Examples:** Six anonymized minimal development examples covering the approved patterns.
4. **Output:** Strict four-field JSON contract with exact evidence requirements.
5. **Input:** Complete posting inside `<posting>...</posting>` as the final message content.

### Acceptance Criteria

1. The public eligibility response has exactly four fields: decision, reason, supporting evidence, and conflicting evidence.
2. The model-facing schema is strict and accepted by LiteLLM/OpenAI structured output.
3. The prompt contains the approved policy and six anonymized development examples only.
4. No prompt example contains case IDs, companies, titles, URLs, or full postings.
5. Missing-information unknown may use empty evidence; every other response has at least one supporting excerpt.
6. Every evidence excerpt passes normalized-exact grounding against the stored posting content.
7. Duplicate evidence and evidence appearing in both buckets are rejected.
8. Reasons are concise, one-line, bounded, and manually reviewed during evaluation.
9. Eligibility persistence stores supporting and conflicting evidence, contract version, and prompt hash.
10. Same-content decisions are reused only under the same contract version and prompt hash.
11. The eligibility worker performs one model call and persists one validated decision atomically.
12. Existing extraction behavior remains gated by a current eligible decision on current content.
13. Facts tables/repositories, deterministic policy, rule traces, and screening stage/CLI are removed.
14. The two untracked SYS-2027 migrations are replaced by one migration from `e7a9b2c4d601`.
15. The 24 tracked atomic-fact files are deleted and the corpus manifest truthfully records that removal.
16. Replay/evaluator/draft tooling measures the one-shot contract and never writes raw responses outside ignored output.
17. Development evaluation uses five replays, reports six example cases separately, and passes all approved gates.
18. Candidate is frozen before exposed-regression evaluation.
19. A fresh blind corpus contains at least 50 unseen postings and at least 10 accepted cases per class.
20. Full Ruff, mypy, tests, migration up/down/up, package, grounding, and security gates pass without provider credentials.
21. Shadow review and explicit bounded activation occur before production rollout.

### Classification

- Type: Architectural replacement and enhancement.
- Complexity: Complex — leaf contract, prompt, persistence, migrations, worker, tools, fixtures, tests, and documentation change.
- Risk: High — the model again authors semantic eligibility; grounding verifies quotations but not their interpretation.

## Phase 2: Codebase Reconnaissance

### Relevant Areas

| Area | Current SYS-2027 behavior | SYS-2028 disposition |
| --- | --- | --- |
| `src/job_eligibility/schema.py` | Thirteen fact variants, normalized facts, deterministic decision | Replace with four-field verdict schema and frozen validated result |
| `src/job_eligibility/prompt.py` | Facts-only extraction prompt | Replace with one-shot context/policy/examples/output/input prompt |
| `src/job_eligibility/validate.py` | Fact canonicalization, grounding, normalization | Reduce to evidence grounding and response consistency |
| `src/job_eligibility/version.py` | Facts version/hash plus screening policy version | Replace with one eligibility contract version/hash |
| `src/job_eligibility/policy.py` | Deterministic OSV2 decision engine | Remove |
| `src/job_eligibility/normalize.py` | Geographic normalization and grounding normalization | Retain only text/layout normalization |
| `worker.py` eligibility path | Facts call, fact reuse, deterministic screen | Replace with compatible decision reuse or one verdict call |
| `job_eligibility_facts` | Durable fact checkpoints | Remove from unshipped schema |
| `job_decisions` | Fact link, rule trace, deterministic reason/evidence | Store verdict reason plus supporting/conflicting evidence and prompt hash |
| Screening stage/CLI | Policy-only preview/apply | Remove |
| Replay/evaluator/draft | Fact metrics and rule IDs | Replace with verdict/evidence metrics |
| v2 corpus | 32 labels plus 24 reviewed facts | Keep labels/text/provenance; delete facts |
| SQLite online backup | Consistent read-only preview | Keep |
| Lease/finalization architecture | Atomic ownership and history | Keep |

### Current Architecture Conflicts

The public leaf currently exposes facts-only types and deterministic screening (`src/job_eligibility/__init__.py:1-113`). Attempts carry fact contract identity, facts are stored in `job_eligibility_facts`, and decisions reference facts and rule traces (`src/job_pipeline/persistence/models.py:206-354`). A complete third `screening/manual` stage also exists. These structures directly conflict with the chosen one-shot architecture and should not remain as dormant parallel paths.

The current SYS-2027 migrations are untracked and were not applied to shared or important databases. The local durable database remains at `e7a9b2c4d601`. Replacing them avoids preserving unshipped complexity and avoids a compensating migration.

### Infrastructure to Keep

- Content acquisition and checkpoints.
- Attempt ownership, leases, retries, interruption recovery, and atomic finalization.
- Decision history and extraction results.
- Composite attempt/job and content/job integrity constraints.
- Transactionally consistent SQLite online backup for read-only planning.
- Safe grounding misses containing path/category/hash/length instead of raw values.
- Atomic, output-contained model recordings and no-overwrite behavior.
- Full migration parity and package tests.

### Constraints

| Constraint | Impact |
| --- | --- |
| `job_eligibility` remains dependency-free | Prompt/schema/grounding/version only; no pipeline imports |
| Model calls never hold DB sessions | Compatible read closes before provider call; final write opens a new session |
| Stage-ending writes are lease-guarded and atomic | Decision/job/attempt transition commits together |
| Existing rows remain auditable | New verdict fields are nullable for legacy rows; no false backfill |
| Examples contaminate development cases | Six example cases excluded from primary tuning score |
| Exposed regression is already known but not tuning data | Freeze candidate before reading/running it |
| Grounding does not prove semantics | Manual reason review and evidence-obligation scoring remain necessary |
| Policy changes require model calls | Remove policy-only screening and document reprocessing cost |

## Phase 3: Gap Analysis and Resolved Questions

### Requirement-to-Code Mapping

| Requirement group | Status | Gap |
| --- | --- | --- |
| One-shot prompt/schema | Conflicts | Current public contract forbids verdict output |
| Supporting/conflicting grounding | Partial | Grounding primitives exist, but are claim-shaped |
| Decision persistence | Partial | Existing decision table can be simplified and extended |
| Worker flow | Conflicts | Current worker requires facts and deterministic policy |
| Screening lifecycle | Conflicts | Must be removed |
| Corpus | Partial | Labels exist; verdict evidence obligations need review/conversion |
| Evaluation | Conflicts | Current metrics are fact/rule based |
| Migration | Missing | Clean SYS-2028 revision must replace untracked SYS-2027 revisions |
| Rollout evidence | Missing | One-shot development, regression, blind, and shadow runs remain |

### Resolved Decisions

| # | Decision | Resolution |
| --- | --- | --- |
| Q1 | Rollout architecture | Replace SYS-2027 immediately |
| Q2 | Output | Decision, reason, supporting evidence, conflicting evidence |
| Q3 | Examples | Six authorized anonymized development examples |
| Q4 | Grounding | Normalized exact, no paraphrases |
| Q5 | Model | `gpt-4.1-mini` only |
| Q6 | Missing-information unknown | Evidence lists may be empty |
| Q7 | Reviewed facts | Delete rather than archive |
| Q8 | Release gate | Same strict safety methodology, adapted to evidence obligations |
| Q9 | Verifier | No second call in SYS-2028 |
| Q10 | Migrations | Not applied to shared/important DB; replace cleanly |
| Q11 | Rule metric | Replace with decision/evidence-role agreement |
| Q12 | Reason semantics | Manual review initially |

### Risk Register

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| Grounded quote assigned wrong meaning | High | High | Reviewed evidence obligations, six examples, five replays, manual reason review, blind corpus |
| Examples overfit development | High | Medium | Anonymize/minimize; exclude six examples from primary score |
| Model ignores conflict evidence | Medium | High | Separate evidence buckets and explicit conflict examples/gates |
| Missing-information unknown fabricates evidence | Medium | Medium | Schema union permits canonical empty-evidence unknown only |
| Old ready jobs cross contract boundary | Medium | High | Require version/hash-compatible decision or explicit eligibility reprocess |
| Removing facts loses cheap policy replay | Certain | Medium | Document model-backed reprocessing and projected cost |
| Migration replacement strands a DB | Low | Critical | Preflight local/shared Alembic heads before deleting revisions |
| Free-form reasons are misleading | Medium | Medium | Bounded one-line format; manual semantic review; evidence remains authoritative |
| Exposed regression influences tuning | Medium | High | Candidate freeze and process guard before evaluation |
| Blind corpus too small for population claims | High | Medium | Report confidence limits and shadow monitoring |

### Phase 3 Gate Check

- Total blocking questions: 0.
- Total non-blocking questions: 0.
- Gate status: OPEN.

## Phase 4: Implementation Plan

### Approach

- Strategy: Incremental replacement behind tests, but no dual production architecture.
- Sequence: Freeze retained corpus → implement and evaluate leaf → replace migration/persistence → replace worker → remove facts/screening → replace tools/tests/docs → run rollout gates.
- Key principle: Validate the one-shot contract offline before deleting the facts runtime, but merge one coherent final architecture.

### Output Contract

```json
{
  "decision": "eligible | ineligible | unknown",
  "reason": "One concise sentence.",
  "supporting_evidence": ["Exact contiguous excerpt"],
  "conflicting_evidence": ["Exact contiguous excerpt"]
}
```

Use a strict schema union:

- **Missing-information unknown:** canonical reason category/text, both evidence lists empty.
- **All other decisions:** supporting evidence has 1–5 excerpts; conflicting evidence has 0–5.
- Evidence excerpts are nonblank and at most 300 characters.
- Reason is one line, bounded, and sentence-terminated.
- No version, hash, confidence, fact, or rule fields are model-authored.

### Prompt Examples

Use six anonymized, minimal, reviewed development patterns:

1. Bare remote with no exclusion → eligible.
2. Explicit remote in foreign-only alternatives → ineligible.
3. Foreign base and foreign authorization; travel contextual → ineligible.
4. Broad LATAM permission conflicting with concrete foreign role location → unknown.
5. Ambiguous location code plus hybrid/remote conflict → unknown.
6. Timezone requirement without residence permission → unknown.

The prompt contains excerpts only, not case IDs, companies, titles, URLs, full postings, or fixture rationales.

### Adapted Quality Gates

Replace facts/rule metrics with:

- 100% schema-valid calls.
- 100% normalized-exact grounding.
- 100% response structural consistency.
- 100% recall of reviewed critical evidence obligations.
- At least 0.90 decision macro F1.
- At least 0.90 exact decision-plus-evidence-role agreement.
- Zero false eligible outcomes on explicit exclusions/conflicts.
- Zero accepted eligible cases classified ineligible.
- Every safety-critical case unanimous over five replays.
- At least 90% of cases unanimous over five replays.
- Manual review of every divergent reason.

For the fresh blind corpus, retain at least 90% decision agreement plus all zero-critical-error and 100%-grounding gates.

**Gate amendment (gate policy 2, operator decision recorded 2026-11-21, before any freeze).** Normalized-exact grounding must reach at least 99% of calls instead of 100%, in the development and blind profiles, and critical evidence recall is measured over grounded calls only (still 100%). Reason: a misquoted excerpt is rejected by validation and retried in production, never stored, so a rare misquote costs a retry rather than a wrong decision, and counting the same call again as an evidence miss double-counts it. Every decision, safety, schema, structural, stability, and manual-review gate is unchanged. The amendment was made after seeing the `gpt-5.4-mini-2026-03-17` medium-effort development run, which missed only by one misquoted RAI02 excerpt (119/120 grounded); that is why the exposed-regression partition and the fresh blind corpus, not the development cases, decide release. Freeze records carry `gate_policy_version`, and a freeze under another policy does not unlock later runs.

### Task Breakdown

#### Task 1: Freeze retained corpus and remove fact artifacts

- Objective: Preserve reviewed decisions and provenance while deleting SYS-2027 facts.
- Files:
  - `tests/fixtures/postings/opportunity-screening-v2/**`
  - `tests/unit/test_opportunity_screening_v2_fixtures.py`
  - `docs/GEOGRAPHIC_ELIGIBILITY_REVIEW.md`
- Changes:
  - [ ] Snapshot current corpus and manifest hashes for audit before deletion.
  - [ ] Delete all 24 `expected_facts.json` files.
  - [ ] Remove facts status/hash sections from the active manifest; retain a concise historical note that SYS-2027 facts were removed by SYS-2028.
  - [ ] Keep all 32 posting texts and `expected_eligibility.json` files unchanged.
  - [ ] Add reviewed supporting/conflicting evidence obligations for development evaluation.
  - [ ] Mark the six authorized example cases and exclude them from primary development scoring.
  - [ ] Preserve exposed-regression IDs without reading/tuning against their contents.
- Dependencies: None.
- Verification: Corpus hashes, decisions, partitions, and v1/v2 lineage remain valid; no fact files remain.
- Effort: 3–4 hours.

#### Task 2: Implement compact one-shot leaf contract

- Objective: Replace the facts API with the four-field verdict contract.
- Files:
  - `src/job_eligibility/schema.py`
  - `src/job_eligibility/prompt.py`
  - `src/job_eligibility/validate.py`
  - `src/job_eligibility/version.py`
  - `src/job_eligibility/__init__.py`
  - reduced `src/job_eligibility/normalize.py`
  - remove `src/job_eligibility/policy.py`
- Changes:
  - [ ] Define strict output variants and evidence constraints.
  - [ ] Build five-section prompt with six anonymized examples.
  - [ ] Preserve untrusted posting boundary.
  - [ ] Implement normalized-exact evidence grounding for both buckets.
  - [ ] Reject duplicates and cross-bucket overlap.
  - [ ] Enforce missing-information unknown empty-evidence form.
  - [ ] Add one-shot version and prompt/schema hash.
  - [ ] Remove all geography normalization, fact types, rule IDs, and deterministic screening exports.
- Dependencies: Task 1 evidence obligations.
- Verification: Schema golden, LiteLLM strict conversion, prompt golden, grounding and injection tests.
- Effort: 4 hours.

#### Task 3: Build one-shot evaluator before pipeline cutover

- Objective: Prove prompt quality independently before changing runtime persistence.
- Files:
  - `scripts/evaluate_leaf.py`
  - `scripts/draft_expectations.py`
  - evaluator tests
- Changes:
  - [ ] Replace fact matching with decision/evidence-role obligations.
  - [ ] Exclude six prompt examples from primary score and report all 24 secondarily.
  - [ ] Record five independent replays bound to model, contract, source hash, and replay index.
  - [ ] Report confusion matrix, decision macro F1, critical evidence recall, grounding, stability, tokens, cost, and latency.
  - [ ] Require manual reason disposition for divergences.
  - [ ] Preserve safe output containment, no overwrite, and sanitized failures.
- Dependencies: Tasks 1–2.
- Verification: Credential-free synthetic tests and provider-free replay tests.
- Effort: 4 hours.

#### Task 4: Tune on development within a two-iteration limit

- Objective: Establish whether the simpler prompt meets development gates.
- Files: Ignored recordings and safe reports only unless a general prompt revision is justified.
- Changes:
  - [ ] Run five replays over the non-example development cases.
  - [ ] Report all-example secondary score separately.
  - [ ] Permit at most two generalized prompt/example revisions.
  - [ ] Never use exposed-regression cases for tuning.
  - [ ] Freeze model, prompt, examples, schema, and hash after the final iteration.
- Dependencies: Task 3.
- Verification: Development gates pass or ticket stops with an explicit negative result.
- Effort: 3–8 hours plus provider cost.

#### Task 5: Replace untracked migrations with one SYS-2028 revision

- Objective: Ship only the schema needed by the one-shot architecture.
- Files:
  - delete `f3b8c1d5e702_add_eligibility_fact_checkpoints.py`
  - delete `b6d4e8f2a913_add_policy_screening_stage.py`
  - new SYS-2028 revision from `e7a9b2c4d601`
  - `src/job_pipeline/persistence/models.py`
- Changes:
  - [ ] Reconfirm no shared/important database references the untracked revisions.
  - [ ] Add nullable attempt eligibility prompt hash for legacy compatibility.
  - [ ] Add decision prompt hash and conflicting evidence JSON.
  - [ ] Keep existing `evidence_json` as supporting evidence storage.
  - [ ] Add composite attempt/job and content/job integrity constraints.
  - [ ] Optionally retain `pipeline_runs.reason` for audited reprocessing.
  - [ ] Do not create facts tables or screening vocabulary.
  - [ ] Preserve legacy rows with null new fields.
- Dependencies: Frozen Task 2 contract.
- Verification: Migration parity, one head, upgrade/down/up, legacy seed preservation, foreign-key checks.
- Effort: 4 hours.

#### Task 6: Simplify decision persistence and worker flow

- Objective: Persist one grounded model verdict atomically.
- Files:
  - `src/job_pipeline/runner/worker.py`
  - `src/job_pipeline/persistence/decisions_repo.py`
  - `src/job_pipeline/persistence/attempts_repo.py`
  - `src/job_pipeline/persistence/models.py`
- Changes:
  - [ ] Reuse only a same-content, same-version, same-hash current decision.
  - [ ] Otherwise call `gpt-4.1-mini` once outside DB sessions.
  - [ ] Validate schema and both evidence buckets.
  - [ ] Persist decision, reason, supporting/conflicting evidence, version, and hash.
  - [ ] Finish attempt and transition job atomically.
  - [ ] Preserve eligible/ineligible/unknown status behavior and extraction gate.
  - [ ] Record extraction attempts against the actual decision contract identity where applicable.
- Dependencies: Tasks 2 and 5.
- Verification: Worker/persistence finalization matrix, stale-owner races, retries, cancellation, token accounting.
- Effort: 4 hours.

#### Task 7: Remove SYS-2027 runtime and screening surface

- Objective: Eliminate parallel facts/policy architecture.
- Files removed:
  - `src/job_pipeline/persistence/facts_repo.py`
  - `src/job_pipeline/persistence/screening_repo.py`
  - `src/job_pipeline/runner/screening.py`
  - `src/job_pipeline/cli/screening.py`
  - `tests/unit/test_pipeline_screening.py`
- Additional cleanup:
  - [ ] Remove facts model/table/link/trace code.
  - [ ] Remove screening stage/phase/manual trigger and retry dispositions.
  - [ ] Remove screening registration, preview sections, and show rendering.
  - [ ] Remove screening-specific result invalidation helpers if unused.
  - [ ] Shrink layering allowlists.
  - [ ] Ensure no dead facts imports or compatibility branches remain.
- Dependencies: Task 6.
- Verification: Search and structural layering tests show no active facts/screening symbols.
- Effort: 3 hours.

#### Task 8: Replace replay and grounding audit tooling

- Objective: Make operational diagnostics match the one-shot contract.
- Files:
  - `scripts/replay_eligibility.py`
  - `scripts/verify_grounding.py`
  - `src/job_pipeline/cli/show.py`
- Changes:
  - [ ] Record/replay verdict outputs with contract version/hash and content identity.
  - [ ] Compare decision and supporting/conflicting evidence distributions.
  - [ ] Verify both persisted evidence buckets against stored content.
  - [ ] Remove fact/rule replay metrics and facts-table grounding audit.
  - [ ] Show supporting/conflicting evidence safely; no posting body or raw response.
- Dependencies: Tasks 5–7.
- Verification: Read-only DB tests, safe report tests, no payload leakage.
- Effort: 3 hours.

#### Task 9: Migrate tests and documentation

- Objective: Make the repository describe and enforce only the one-shot architecture.
- Files:
  - eligibility unit tests and schema golden
  - pipeline worker/persistence/finalization/CLI/stage/scheduler tests
  - layering and migration tests
  - README, ARCHITECTURE, CLAUDE, TABNINE
  - remove/supersede `spike/SYS_2027_PLAN.md`
- Changes:
  - [ ] Remove facts/policy/screening tests.
  - [ ] Preserve valuable grounding, atomicity, lease, migration, and security cases.
  - [ ] Update architecture graph and versioning rules.
  - [ ] Document model-authored semantics and grounding limitations honestly.
  - [ ] Document that policy changes require model-backed reprocessing.
  - [ ] Keep CLAUDE/TABNINE project facts synchronized.
- Dependencies: Tasks 2–8.
- Verification: Documentation search contains no active SYS-2027 architecture claims.
- Effort: 4 hours.

#### Task 10: Run repository quality gates

- Objective: Verify the replacement implementation offline.
- Commands:
  - [ ] `uv run ruff format . && uv run ruff check . --fix`
  - [ ] `uv run mypy`
  - [ ] `uv run --group test pytest tests/`
  - [ ] Alembic upgrade/down/up and one-head checks
  - [ ] `uv run python scripts/verify_grounding.py`
  - [ ] Build/install wheel and exercise all console commands
  - [ ] Confirm no dependency/lock change or perform CVE review and log mitigation
- Dependencies: Tasks 1–9.
- Verification: All commands pass without provider credentials.
- Effort: 2–4 hours.

#### Task 11: Freeze candidate and evaluate exposed regression

- Objective: Measure stability after development tuning without further changes.
- Changes:
  - [ ] Freeze prompt, examples, schema, model, settings, version, and hash.
  - [ ] Run five replays over the exposed regression partition.
  - [ ] Report results separately and make no tuning changes from them.
  - [ ] Obtain operator disposition for every divergence and reason.
- Dependencies: Development gates pass.
- Verification: Regression report is complete and candidate remains unchanged.
- Effort: 2–4 hours plus provider cost.

#### Task 12: Collect and freeze the fresh blind corpus

- Objective: Build release evidence independent of prompt construction.
- Changes:
  - [ ] Collect at least 50 fresh public postings unseen in prior work.
  - [ ] Require at least 10 accepted eligible, 10 ineligible, and 10 unknown cases.
  - [ ] Label decisions and supporting/conflicting evidence obligations before model execution.
  - [ ] Freeze content, labels, provenance, partitions, and hashes.
  - [ ] Keep prompt examples and prior corpus out of blind inputs.
- Dependencies: Candidate frozen.
- Verification: Operator-reviewed blind manifest before any call.
- Effort: 8–16 hours review time.

#### Task 13: Blind evaluation, shadow, and activation

- Objective: Decide whether the one-shot system is safe to deploy.
- Changes:
  - [ ] Run five replays over every blind case.
  - [ ] Enforce all grounding, evidence, decision, safety, and stability gates.
  - [ ] Manually review reasons and divergences.
  - [ ] Shadow against fresh live postings without changing durable decisions.
  - [ ] Back up the database.
  - [ ] Explicitly reprocess old ready/current decisions under the new contract in bounded batches.
  - [ ] Activate extraction only after operator approval.
- Dependencies: Tasks 10–12.
- Verification: Blind and shadow reports approved; no stale-contract ready jobs remain.
- Effort: 4–8 hours plus provider/runtime cost.

### Testing Strategy

| Test Type | Scope | Priority |
| --- | --- | --- |
| Unit | Strict verdict schema, prompt examples, normalized grounding, evidence bucket consistency | P0 |
| Unit | Version/hash and LiteLLM strict conversion | P0 |
| Integration | One-call worker transitions and compatible decision reuse | P0 |
| Integration | Atomic persistence, stale ownership, retries, cancellation | P0 |
| Migration | Clean replacement migration, legacy rows, up/down/up, one head | P0 |
| Corpus | Decision labels, evidence obligations, example exclusion, no secrets | P0 |
| Replay | Five-call distributions, safe recordings, no overwrite, content identity | P0 |
| Development model | Primary non-example and secondary all-development scores | P0 |
| Regression model | Frozen candidate only | P0 |
| Blind model | ≥50 unseen balanced cases and strict gates | P0 |
| Package | Clean wheel and console smoke | P1 |
| Manual | Reason review, shadow disagreements, bounded reprocessing | P0 |

### Required Edge Cases

- Bare remote with no exclusion.
- Explicit Costa Rica.
- LATAM/Americas/worldwide/anywhere permission.
- Foreign-only remote alternatives.
- Mandatory foreign residence, work location, authorization, and recurring onsite work.
- Preferences that do not restrict.
- Timezone-only context.
- Client, market, employer, or team geography.
- Foreign role header without restrictive wording.
- Concrete broad permission versus narrower foreign conflict.
- Hybrid/remote arrangement conflict and ambiguous location code.
- Missing role geography.
- Missing-information unknown with empty evidence.
- Unknown with conflicting evidence in both buckets.
- Evidence paraphrase, combined text, quote wrappers, duplicated evidence, and cross-bucket overlap.
- Prompt injection inside posting content.
- Same content under changed prompt hash.
- Legacy decisions with null prompt hash.
- Old ready job requiring eligibility reprocess before extraction.

### Definition of Done

- [ ] SYS-2027 fact and screening runtime is fully removed.
- [ ] One-shot schema and prompt are the only public eligibility contract.
- [ ] Six authorized examples are anonymized and excluded from the primary score.
- [ ] Supporting and conflicting evidence ground exactly under approved normalization.
- [ ] Decisions persist contract version/hash and both evidence buckets.
- [ ] Worker performs one model call and atomic finalization.
- [ ] Facts and screening migrations are replaced by one clean migration from `e7a9b2c4d601`.
- [ ] 24 reviewed fact files are deleted by explicit user decision.
- [ ] 32 posting texts, labels, provenance, and splits remain intact.
- [ ] Development evaluation passes the adapted strict gates.
- [ ] Candidate is frozen before exposed-regression evaluation.
- [ ] Full offline, migration, grounding, package, and security gates pass.
- [ ] Fresh balanced blind corpus is frozen before execution.
- [ ] Blind evaluation passes all release gates.
- [ ] Shadow review and manual reason review are approved.
- [ ] Old ready/current jobs are explicitly reprocessed under the new contract before activation.

## Suggested Next Steps

**Start Implementation:** Begin with Task 1 and Task 2, preserving the reviewed decision corpus while replacing the leaf contract.

**Create Tests First:** Write the strict one-shot schema, prompt-example, grounding, and evidence-bucket tests before changing production code.
