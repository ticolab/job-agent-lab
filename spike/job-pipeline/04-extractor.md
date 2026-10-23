# Phase 4 — The `job_extractor` leaf

Goal: a pure library that turns posting text into a structured, grounded job
result with explicit field states, plus reviewed expectations and the
evaluation extension. Two pull requests, same split as Phase 3.

The leaf never invents. The validator can only lower a field's state, never
raise it, and a list may be empty. The dashboard decides how much to show.

## Task 4.1 — Field state and value types

- Objective: one generic shape for every fact.
- Files: `src/job_extractor/schema.py`, `tests/unit/test_extractor_schema.py`.
- Changes:
  - [ ] `FieldState = Literal["extracted", "not_stated", "uncertain", "failed"]`.
        The model may return the first three; only the validator writes
        `failed`.
  - [ ] `Fact[T](state, value: T | None, evidence: list[str])` with a model
        validator: `extracted` requires a value and at least one quote;
        `not_stated` requires no value and no quotes; `uncertain` requires
        at least one quote.
  - [ ] Enumerations, each with the `not_stated` case carried by the state
        rather than by an enum member:
        - `WorkMode`: remote, hybrid, onsite.
        - `EmploymentType`: full_time, part_time, contract, freelance,
          temporary, internship.
        - `ExperienceLevel`: entry, junior, mid, senior, lead, principal,
          executive.
        - `JobFunction`: the legacy sixteen, with "data science and
          machine learning" moved under Data & Analytics and Technology &
          Engineering described as software, infrastructure, security, QA,
          and architecture. Multiple allowed.
  - [ ] `Qualification(text, kind: Literal["skill","experience","education",
        "certification","language","behavioral","other"], evidence)`.
  - [ ] `TechnologyMention(mention, status: Literal["required","preferred",
        "mentioned"], evidence)` — the model's view, before normalization.
  - [ ] `ExtractionResponse`: `title`, `location_text`, `location:
        Fact[Location]` where `Location(country, region, city)` requires at
        least `country`; `work_mode`, `employment_type`, `experience_level`,
        `job_functions: Fact[list[JobFunction]]`, `summary: Fact[str]`,
        `source_language: Fact[str]` (ISO 639-1), `responsibilities:
        Fact[list[str]]`, `required_qualifications: Fact[list[Qualification]]`,
        `preferred_qualifications: Fact[list[Qualification]]`, `benefits:
        Fact[list[str]]`, `compensation_text: Fact[str]`, `technologies:
        Fact[list[TechnologyMention]]`, `posting_date: Fact[date]`,
        `requisition_id: Fact[str]`.
  - [ ] `JobResult(frozen)`: the validated, normalized output —
        `ExtractionResponse` fields plus `technologies_normalized:
        list[Technology]`, `extractor_version`, `schema_version`,
        `validation_notes`.
  - [ ] `SCHEMA_VERSION = 1`; JSON schema export golden file.
- Dependencies: Phase 1.
- Verification: round-trip tests; each `Fact` validator rule; schema golden.
- Effort: 4 hours.

## Task 4.2 — Prompt

- Objective: instructions that produce the schema honestly.
- Files: `src/job_extractor/prompt.py`, `src/job_extractor/version.py`,
  `tests/unit/test_extractor_prompt.py`.
- Changes:
  - [ ] Clause constants, fixed order. Content rewritten from
        `prompts/stage_2_details.md` and `stage_3_skills.md` with these
        rules applied:
        - Report only what the posting states; use `not_stated` freely.
        - Section headings decide required versus preferred; an unlabeled
          requirements list is required unless the item says "preferred",
          "a plus", "nice to have", "ideally", or "bonus".
        - Duties mixed into a requirements list are responsibilities only
          when phrased as an action the person will perform.
        - Experience level: title words outrank years. Years map entry 0 to
          1, junior 1 to 3, mid 3 to 5, senior 5 and above; lead,
          principal, executive by role words only.
        - Work mode only from explicit wording; a city name alone is
          `not_stated`.
        - Technologies: every named language, framework, platform,
          database, cloud service, or tool anywhere in the posting, with
          the sentence it appears in; `required` or `preferred` from the
          section, `mentioned` when it appears only in duties or company
          text.
        - Source wording verbatim in its language; summary in English, two
          sentences, factual, no marketing adjectives.
        - No capitalization changes, no rewording, no minimums, no caps.
  - [ ] `build_messages(posting_text)` with the same untrusted-data wrapper
        as the eligibility leaf.
  - [ ] `EXTRACTOR_VERSION = "2026.10.1"`, `PROMPT_HASH` pinned by a test
        exactly as in Phase 3.
  - [ ] E501 per-file ignore for `prompt.py`.
- Dependencies: 4.1.
- Verification: prompt golden; hash-pin test.
- Effort: 3 hours.

## Task 4.3 — Technology normalization

- Objective: canonical names decided by code, never by the model.
- Files: `src/job_extractor/normalize.py`,
  `src/job_extractor/data/technologies.json`,
  `tests/unit/test_extractor_normalize.py`.
- Changes:
  - [ ] `technologies.json`: a list of `{canonical, aliases, kind}` entries
        seeded from the rules in `prompts/stage_4_tech.md`, with two
        corrections: AngularJS and Angular are separate entries; plain
        "SQL" maps to a `kind: "concept"` entry, never to a vendor product.
        Version suffixes are stripped by a regex before lookup
        (`Python 3.11` to `python`), but `.NET Core` and `.NET Framework`
        remain distinct from `.NET` only if the operator decides so during
        review; default keeps them as aliases of `.NET` per the legacy rule.
  - [ ] `normalize(mentions) -> list[Technology]` where
        `Technology(canonical, kind, status, mentions: list[TechnologyMention])`:
        group by canonical; `required` outranks `preferred` outranks
        `mentioned`; every original mention and its evidence is kept.
        Unmatched mentions become `Technology(canonical=None, ...)` with
        the raw mention, so nothing is dropped and the gap list drives
        table growth.
  - [ ] The JSON file ships in the wheel: add it to the hatch `include`
        list and load it with `importlib.resources`, the same mechanism as
        the matcher JS.
  - [ ] Tests: alias hits, version stripping, the AngularJS split, the
        SQL concept, precedence, unmatched preservation, and a test that
        aliases are unique across entries.
- Dependencies: 4.1.
- Verification: unit tests; the unmatched list over the corpus is empty or
  reviewed into the table.
- Effort: 4 hours.

## Task 4.4 — Validation

- Objective: accept a result only when it is grounded and internally
  consistent, downgrading single fields rather than discarding the whole.
- Files: `src/job_extractor/validate.py`, `tests/unit/test_extractor_validate.py`.
- Changes:
  - [ ] `validate(response, posting_text) -> JobResult`.
  - [ ] Grounding per fact: a fact with any ungrounded quote becomes
        `failed` with a note naming the quote; other facts are untouched.
        The result as a whole is rejected with `UngroundedEvidenceError`
        only when `title` or more than a third of the extracted facts fail,
        which signals a response built on the wrong text.
  - [ ] Consistency: a technology marked `required` must trace to a
        required qualification or a required-section quote; otherwise it is
        lowered to `mentioned` with a note. `preferred_qualifications` and
        `required_qualifications` may not share an identical text.
        `location.state` may not be `extracted` when `location_text` is
        `not_stated`.
  - [ ] `summary` is checked for fabrication by requiring every proper
        noun in it (capitalized token not at sentence start) to occur in
        the posting text; a miss lowers the summary to `uncertain`.
  - [ ] Normalization from 4.3 is applied here so the leaf has one
        entry point: `validate` returns the normalized technologies.
- Dependencies: 4.1 to 4.3.
- Verification: unit tests per rule, plus one recorded response with a
  deliberately altered quote that must come back `failed` on that field
  only.
- Effort: 4 hours.

## Task 4.5 — Golden expectations for extraction

- Objective: reviewed truth for the twenty-five eligible or unknown corpus
  postings (the ineligible ones are never extracted).
- Files: `tests/fixtures/postings/<dir>/expected_extraction.json`,
  `scripts/draft_expectations.py` (extend with `--leaf extractor`).
- Changes:
  - [ ] Draft through a `RecordingCaller`, then operator review with
        `"reviewed": true`.
  - [ ] Expected shape is per component so Phase 6 can score each one:
        `metadata` (exact enum values and states), `responsibilities`,
        `required_qualifications`, `preferred_qualifications`, `benefits`
        (each a list of normalized strings matched by casefolded exact
        text), `technologies` (set of canonical names with status).
  - [ ] Sparse postings assert `not_stated` for benefits, and the posting
        with duties mixed into requirements asserts the split.
- Dependencies: 4.2 to 4.4, Phase 2 corpus, Phase 3 client.
- Verification: twenty-five reviewed files.
- Effort: 2 hours of script, 5 hours of operator review.

## Task 4.6 — Evaluation and replay

- Objective: component-level scores and a key-free regression test.
- Files: `scripts/evaluate_leaf.py` (extend), `tests/unit/test_extractor_golden.py`.
- Changes:
  - [ ] `--leaf extractor` prints per component: metadata exact-match rate
        per field; precision and recall for each list component by
        casefolded exact match; technology set precision and recall; count
        of `failed` fields; grounding rejections; tokens and estimated cost.
  - [ ] Acceptance thresholds, exit code enforced: title 100 percent;
        work mode, employment type, experience level each at least 90
        percent exact; responsibilities and required qualifications recall
        at least 0.9 and precision at least 0.9; preferred qualifications
        and benefits recall at least 0.85; technologies recall at least
        0.9, precision at least 0.9; zero grounding rejections.
  - [ ] Replay test runs `validate` over recorded responses and compares
        with reviewed expectations.
- Dependencies: 4.5.
- Verification: thresholds met on the default model; replay test green.
- Effort: 4 hours.

## Testing strategy

| Test type | Scope | Priority |
|---|---|---|
| Unit | fact state rules, enums, schema golden, prompt golden, version pin | P0 |
| Unit | normalization table, precedence, unmatched preservation | P0 |
| Unit | every validation downgrade and the whole-result rejection | P0 |
| Unit | replay against reviewed expectations | P0 |
| Manual | `evaluate_leaf.py --leaf extractor` thresholds | P0 |

## Done

- [ ] PR 1 `feat: add the job_extractor leaf with field states and technology normalization`.
- [ ] PR 2 `test: add reviewed extraction expectations and component-level evaluation`.
- [ ] Unmatched technology mentions over the corpus reviewed into the table or recorded as intentional.
- [ ] `prompts/stage_3_skills.md` and `stage_4_tech.md` marked superseded in `MANIFEST.md`.
