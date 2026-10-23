# Job Extractor Requirements

## Status and purpose

This document preserves the product requirements that led to the job pipeline. The approved implementation decisions and phased status live in `spike/job-pipeline/`, beginning with `00-overview.md`.

The decision log in that directory resolves or deliberately defers the former open items for v1. This file remains the statement of desired outcomes; the spike directory is the statement of architecture and implementation scope. The previous prompt-based application is historical evidence, not a runtime contract.

## Background

`job-discovery` scans company career sources and produces the current direct job-posting URLs. It answers which postings are currently visible but does not extract each posting’s details.

The intended daily workflow is:

1. Refresh the current posting URLs with `job-discovery`.
2. Exclude logical jobs that already have a completed outcome.
3. Evaluate the eligibility of new jobs.
4. Fully extract eligible jobs.
5. Preserve durable processing history so completed jobs are not repeatedly evaluated.

A previous implementation fetched raw HTML and sent large portions of it to OpenAI prompts. It eventually required company-specific HTML tags or selectors to isolate useful content. That integration and maintenance effort did not scale. The new product must reduce company-specific work without prescribing the technical method prematurely.

## Product boundary

`job-discovery` owns discovery of current posting URLs. `job-extractor` owns job-processing history, eligibility outcomes, and structured job details.

Extraction must be runnable independently from discovery. Whether the capabilities ultimately use separate executables, packages, repositories, databases, or deployment processes is an architecture decision outside this document.

## Confirmed goals

The product shall:

1. Process newly discovered logical jobs without repeatedly processing completed jobs.
2. Treat a definitive ineligible result as completed, so later daily runs do not check the same job again.
3. Determine eligibility before accepting a full enriched result.
4. Extract structured metadata, responsibilities, qualifications, benefits, and technologies for eligible jobs.
5. Minimize source-specific integration and recurring maintenance.
6. Isolate item failures so one broken posting does not stop unrelated work.
7. Preserve auditable outcomes for eligible, ineligible, unknown, unavailable, and failed jobs.
8. Represent missing information honestly rather than inventing content.

## Non-goals

The first version is not required to:

1. Discover posting URLs from listing pages.
2. Submit job applications.
3. Rank candidates against jobs.
4. Generate resumes, cover letters, or outreach.
5. Monitor changes to a successfully extracted job description.
6. Re-evaluate completed ineligible jobs during every daily run.
7. Retain complete raw pages indefinitely.
8. Select the implementation architecture in this document.

## Definitions

| Term | Meaning |
|---|---|
| Discovered source | A direct posting URL supplied by `job-discovery`. |
| Logical job | The real posting represented by one or more discovered source URLs. |
| Observation | A discovered source presented to the extractor at a point in time. |
| Processing attempt | One effort to retrieve, evaluate, and possibly enrich a logical job. |
| Completed job | A logical job with an accepted terminal outcome that ordinary runs skip. |
| Eligible | Authoritative evidence satisfies the configured eligibility policy. |
| Ineligible | Authoritative evidence definitively violates the configured eligibility policy. |
| Unknown eligibility | Available evidence is missing, ambiguous, or contradictory. |
| Unavailable | The posting cannot currently be obtained as an active job posting. |
| Retryable failure | A technical failure for which another automatic attempt is owed. |
| Terminal failure | A failure that exhausted its policy and requires explicit reprocessing. |
| Source evidence | Posting content supporting an eligibility decision or extracted fact. |
| Ordinary run | Routine incremental execution without an explicit reprocessing request. |

A job is not considered completed merely because an attempt started. Completed eligible and completed ineligible jobs are skipped by ordinary runs. For v1, D8 makes unknown and unavailable terminal, retries classified technical failures three times with linear daily backoff, and makes exhausted failures terminal.

## Input and source requirements

### REQ-IN-1: Discovery input

The extractor shall consume direct posting URLs from `job-discovery` and the associated company identity needed to preserve source context.

### REQ-IN-2: Transient discovery records

Previously processed detection shall not depend on `job_urls.id`. Discovery replaces a company’s current URL rows after successful scans, so row identifiers are transient and the current table is not historical.

### REQ-IN-3: Authoritative content

The extractor shall obtain sufficient authoritative posting content to decide eligibility and populate the required fields. It shall support sources whose complete content is not present in initial server HTML.

### REQ-IN-4: Explicit source outcomes

Closed, missing, redirected, blocked, authenticated, malformed, and otherwise inaccessible postings shall produce explicit outcomes. Missing content shall not be silently classified as ineligible or as a successful empty extraction.

## Logical identity and completed-job filtering

### REQ-ID-1: Durable processing history

The product shall retain processing history independently of the current discovery snapshot. A completed job shall remain known after its URL disappears from `job-discovery`.

### REQ-ID-2: Eligibility-independent suppression

Ordinary runs shall skip a logical job that already has a completed outcome. This includes both fully extracted eligible jobs and definitively ineligible jobs.

The rule is “this logical job already completed,” not “this job was eligible and extracted.”

### REQ-ID-3: Stable logical identity

The product shall identify the same logical job across discovery scans without relying only on a transient row identifier or exact raw URL text.

The identity policy must address redirects, fragments, tracking parameters, equivalent URL variants, provider requisition identifiers, company-name changes, and mirrored sources.

### REQ-ID-4: Conservative deduplication

When evidence is insufficient to prove that two observations represent the same logical job, the product shall not silently merge them. It shall preserve enough information for later review or reconciliation.

### REQ-ID-5: Reused URLs

A source URL reused for a different authoritative requisition or posting shall be treated as a new logical job. A completed outcome for an older posting shall not suppress the new posting merely because the URL text is unchanged.

### REQ-ID-6: Idempotency

Repeated or overlapping execution against the same observations shall not create duplicate logical jobs or more than one accepted ordinary terminal result for the same logical job and processing context.

### REQ-ID-7: Explicit reprocessing

The product shall provide an explicit, auditable way to reprocess a completed job. Reprocessing shall preserve prior outcomes and identify its trigger, such as operator investigation or a changed extraction or eligibility policy.

Ordinary execution shall not reprocess completed jobs automatically unless a future approved requirement defines such a trigger.

## Processing lifecycle requirements

### REQ-LIFE-1: Distinct lifecycle concepts

The lifecycle shall distinguish observation, pending work, active processing, completed outcomes, retryable failures, and terminal failures. It shall not equate “observed once” with “successfully handled.”

### REQ-LIFE-2: Outcome classification

Every attempt shall end with one of these conceptual outcomes:

| Outcome | Meaning | Ordinary-run behavior |
|---|---|---|
| Completed eligible | Eligibility succeeded and required enrichment completed. | Skip. |
| Completed ineligible | Eligibility definitively failed. | Skip. |
| Unknown eligibility | A trustworthy decision could not be made from the evidence. | Skip unless explicitly reprocessed (D8). |
| Unavailable | The source was not an obtainable active posting. | Skip (D8). |
| Retryable failure | A technical problem prevented completion. | Retry under policy. |
| Terminal failure | Retry policy was exhausted or the failure was declared permanent. | Skip unless explicitly reprocessed. |
| Interrupted | Processing ended without a final outcome. | Resume or retry. |

### REQ-LIFE-3: Phase preservation

If eligibility succeeds but enrichment fails, the successful eligibility result and its evidence shall remain available. A retry shall not repeat completed work unnecessarily when the same policy and source evidence remain applicable.

### REQ-LIFE-4: Failure isolation

Failure to process one job shall not abort unrelated jobs.

### REQ-LIFE-5: Failure details

A technical failure shall identify its phase and retain a useful category and message. Relevant phases include source retrieval, eligibility, enrichment, validation, and result acceptance.

### REQ-LIFE-6: Retry safety

Retries shall not create duplicate jobs or accepted results. A retryable outcome shall retain enough information to determine its attempt history and whether another attempt is owed.

D8 resolves v1 retries: retry classified technical failures up to three attempts with linear daily backoff, then mark them terminal. Ordinary work is owed only while its URL key remains in the current discovery snapshot.

### REQ-LIFE-7: Restart safety

After interruption, later execution shall recover outstanding work without losing completed outcomes or requiring one process to remain alive between daily runs.

## Eligibility requirements

### REQ-ELIG-1: Eligibility gate

Ordinary full enrichment shall proceed only for jobs classified as eligible. Shared source acquisition or analysis may support both phases, but an ineligible job shall not be accepted as fully enriched.

### REQ-ELIG-2: Decision values

Eligibility shall support:

| Decision | Meaning |
|---|---|
| Eligible | All required policy predicates are satisfied by authoritative evidence. |
| Ineligible | Authoritative evidence positively contradicts at least one required predicate. |
| Unknown | Evidence is absent, ambiguous, or contradictory. |
| Failed | A technical error prevented a trustworthy evaluation. |

Unknown and failed shall not be treated as ineligible.

### REQ-ELIG-3: Reason and evidence

Every accepted eligible or ineligible decision shall retain a concise reason, the relevant source evidence, and the eligibility-policy version. An unknown decision shall identify what evidence was missing or contradictory.

### REQ-ELIG-4: No invented facts

The product shall not invent location, work authorization, residency permission, remote-work permission, or timezone compatibility. Missing or ambiguous evidence shall remain unknown.

### Candidate eligibility policy requiring confirmation

The legacy prompts evaluated whether a job was available to a person working from Costa Rica. Under that candidate policy:

- Explicit Costa Rica availability is eligible.
- Worldwide or global availability is eligible unless another restriction conflicts.
- LATAM availability without an exhaustive country restriction is eligible.
- An exhaustive permitted-country list that excludes Costa Rica is ineligible.
- Explicit restriction to another location is ineligible.
- Preference language and example locations are not automatically restrictions.
- Contradictory or unclear statements result in unknown eligibility; role-specific text outranks generic employer text under the versioned policy.

The final policy must separately address work location, residency, work authorization, remote-work restrictions, and timezone requirements. Absence of explicit permission is not by itself proof of exclusion.

## Eligible-job data requirements

A completed eligible result shall include source identity, structured fields, field states, and provenance. The storage schema is outside this document.

### REQ-DATA-1: Source identity

The result shall retain:

| Field | Requirement |
|---|---|
| Source URL | The discovered URL processed. |
| Resolved URL | The authoritative final URL after redirects, when different. |
| Company identity | The company supplied by discovery. |
| Source content identity | A way to identify the exact content version evaluated. |
| Retrieved time | When the source content was obtained. |
| Extracted time | When structured extraction completed. |
| Processing versions | The applicable eligibility, extraction, and result-schema versions. |

### REQ-DATA-2: Field states

Each expected field shall distinguish among:

- extracted from the source;
- explicitly not stated by the source;
- uncertain or contradictory;
- extraction failed for that field.

Source absence shall not be used to hide an extraction failure.

### REQ-DATA-3: Core metadata

The product shall attempt to extract these fields, with unknown or not-stated values permitted until the mandatory-field matrix is approved:

| Field | Requirement |
|---|---|
| Job title | The source role title. |
| Location text | Location wording as stated. |
| Normalized location | Structured location when supported by evidence. |
| Work mode | Remote, hybrid, onsite, or unknown. |
| Employment type | Normalized value plus source wording. |
| Experience level | Normalized value plus supporting evidence. |
| Job function | One or more normalized D6 categories. |
| Summary | Concise factual role summary. |
| Source language | Detected or declared language when available. |

### REQ-DATA-4: Responsibilities

The product shall return explicitly stated job duties as a structured collection. Requirements mixed into a duties section shall not be mislabeled as responsibilities.

Responsibilities shall not be generated when the source provides none.

### REQ-DATA-5: Required qualifications

The product shall return explicitly required skills, experience, education, certifications, qualifications, and relevant behavioral requirements. Source wording and evidence shall remain available alongside normalized representations.

### REQ-DATA-6: Preferred qualifications

Preferred, optional, bonus, or nice-to-have qualifications shall be separate from mandatory requirements. Ambiguous language shall not silently become mandatory.

### REQ-DATA-7: Benefits and compensation

Benefits, perks, and compensation statements shall be extracted only when explicit in the source. Standard benefits shall not be inferred from the company, location, or industry.

A structured salary contract remains an open requirement.

### REQ-DATA-8: Technologies

The product shall identify technologies explicitly relevant to the role, including languages, frameworks, platforms, databases, cloud services, and named technical tools.

Each technology result shall support:

| Field | Requirement |
|---|---|
| Source mention | Original wording. |
| Normalized name | Canonical name when confidently mapped. |
| Requirement status | Required, preferred, mentioned, or unknown. |
| Evidence | Supporting source statement or locator. |

When the same technology is both required and preferred, the result shall contain one normalized technology and preserve evidence for both mentions. Required status takes precedence for filtering.

### REQ-DATA-9: Principal technologies

The product may identify a smaller explainable set of technologies central to the role. Every principal technology shall also appear in the complete technology collection.

Principal technologies are omitted from v1 because no downstream purpose is defined.

### REQ-DATA-10: No fabricated content

The product shall not fabricate metadata, responsibilities, qualifications, benefits, compensation, or technologies to satisfy required fields or minimum list sizes.

### REQ-DATA-11: Cardinality

No minimum item count is required for responsibilities, qualifications, benefits, or technologies. The previous minimums conflicted with source fidelity.

V1 imposes no collection maximum. A future limit requires a downstream use case and shall not silently discard required source facts.

### REQ-DATA-12: Completeness

An eligible result is complete when every schema field has a valid field state and the result passes deterministic validation. A field-level failure is retained honestly; title failure or excessive grounding failure rejects the whole response.

## Provenance and validation requirements

### REQ-PROV-1: Field provenance

Every accepted normalized fact shall retain its original source wording and enough evidence to explain the extraction. Every eligibility decision shall reference evidence from the exact source-content version processed.

### REQ-PROV-2: Validation

Structured output shall be validated before acceptance. Malformed output, missing source identity, unsupported normalized values, internal inconsistency, or an extraction error shall not be accepted as successful completion.

Contradictory source statements may validly produce unknown values; they are not themselves malformed output.

### REQ-PROV-3: Historical outcomes

Reprocessing shall create a new auditable attempt. Prior decisions and extracted results shall remain distinguishable from the current accepted result.

### REQ-PROV-4: Retention

V1 retains durable source evidence, normalized content versions, attempts, decisions, and results. Complete raw-page retention periods remain outside v1; evidence must remain available with its auditable result.

## Source integration constraints

### REQ-SOURCE-1: Low integration effort

A newly supported source shall meet an agreed onboarding-time and recurring-maintenance budget without routine source-specific code or configuration changes.

The previous requirement to locate and hard-code company-specific HTML containers is not acceptable as the normal onboarding path.

### REQ-SOURCE-2: Relevant authoritative content

Irrelevant page material such as navigation, scripts, styles, headers, footers, and legal text shall not materially reduce extraction quality or cause unacceptable variable cost.

### REQ-SOURCE-3: Source diversity

The product shall obtain complete authoritative posting content whether a source exposes structured data, server-rendered content, client-rendered content, or redirects to another posting host.

### REQ-SOURCE-4: Prompt independence

The historical files under `prompts/` are product evidence, not immutable runtime contracts. The final product need not execute them as separate stages or pass raw HTML directly into them.

## Operational requirements

### REQ-OPS-1: Independent execution

Extraction shall run independently from discovery. Discovery and extraction may be scheduled, interrupted, and retried separately.

### REQ-OPS-2: Incremental daily operation

An ordinary daily run shall process only new or explicitly retryable work. It shall skip completed logical jobs regardless of whether they completed as eligible or ineligible.

### REQ-OPS-3: Workload and source limits

Execution shall meet the agreed daily completion target while respecting configured source and provider limits. The target and limits remain open requirements.

### REQ-OPS-4: Preview

An operator should be able to preview how many observations map to completed, new, retryable, active, or unresolved work without incurring variable-cost extraction.

### REQ-OPS-5: Selection

An operator should be able to restrict an investigation by company or job without changing ordinary completed-job suppression unless explicit reprocessing is requested.

### REQ-OPS-6: Run summary

A run summary shall reconcile candidates into clearly defined dispositions, including completed jobs skipped, jobs attempted, eligible completions, ineligible completions, unknown decisions, unavailable jobs, retryable failures, terminal failures, and interrupted work.

### REQ-OPS-7: Auditability

For any logical job, an operator shall be able to determine its observations, attempts, outcomes, reasons, evidence, applicable versions, reprocessing history, and current accepted result.

### REQ-OPS-8: Cost visibility

If extraction incurs variable model, browser, or API cost, usage shall be observable by run and processing outcome. Acceptable cost and latency budgets remain open requirements.

## Quality and security requirements

### REQ-QUAL-1: Source fidelity

Extracted facts shall be grounded in the posting. Normalization shall not erase original wording needed for audit or later reinterpretation.

### REQ-QUAL-2: Repeatability

Quality evaluation shall be repeatable and isolated from live-source drift, transient availability, and variable external cost.

### REQ-QUAL-3: Component-level measurement

Quality shall be measured separately for logical identity, eligibility, metadata, responsibilities, required qualifications, preferred qualifications, benefits, and technologies. A single overall success rate is insufficient.

### REQ-QUAL-4: Uncertainty

Uncertain inferences shall not be presented as certain facts. The representation of confidence remains open, but explicit unknown and evidence states are required.

### REQ-SEC-1: Untrusted content

Posting content is untrusted input. It shall not override system instructions, access secrets, execute arbitrary code, authorize unsafe actions, or cause unauthorized data disclosure.

### REQ-SEC-2: Secret handling

Secrets shall not appear in extracted data, retained evidence, logs, reports, or evaluation fixtures.

## Acceptance scenarios

### Scenario A: Previously completed eligible job

A logical job appears in today’s discovery output and already has a completed eligible extraction. The ordinary run skips it and creates no duplicate result.

### Scenario B: Previously completed ineligible job

A logical job appears again after a definitive ineligible decision. The ordinary run skips it without checking eligibility again.

### Scenario C: New eligible job

A new logical job receives an eligible decision with evidence and completes all mandatory enrichment. One completed eligible result is accepted.

### Scenario D: New ineligible job

A new logical job is definitively ineligible. The result retains its reason, evidence, and policy version. Full enrichment is not accepted, and later ordinary runs skip it.

### Scenario E: Eligibility succeeds but enrichment fails

The eligible decision and evidence remain available. The job stays incomplete and retryable according to policy. A retry does not repeat eligibility unnecessarily while the same policy and source evidence remain applicable.

### Scenario F: Temporary retrieval failure

A new job times out. The attempt records a retryable technical failure rather than ineligibility or completion. The job remains owed another attempt under policy.

### Scenario G: Unknown eligibility

A posting contains contradictory or insufficient location evidence. The result is unknown rather than ineligible. V1 treats it as terminal until an operator explicitly reprocesses it after review or a policy change.

### Scenario H: Closed before processing

A discovered posting closes before extraction. The condition is recorded explicitly and does not remain indistinguishable from unseen work. Retry or terminal behavior follows the unavailable policy.

### Scenario I: Equivalent URL variants

Tracking, fragment, or redirect variants proven to identify the same posting produce one logical job while preserving all observed source URLs.

### Scenario J: Reused URL

A company reuses a URL for a different requisition. The new requisition becomes a new logical job and is not suppressed by the old posting’s completed outcome.

### Scenario K: Overlapping runs

Two executions encounter the same new logical job. They produce no more than one accepted ordinary terminal result for the same processing context.

### Scenario L: Sparse source

An eligible posting contains two responsibilities and no benefits. The result preserves the two responsibilities, marks benefits as not stated, and invents nothing.

### Scenario M: Field extraction failure

The source states benefits, but benefits extraction fails. The result distinguishes extraction failure from benefits not being stated.

### Scenario N: Explicit reprocessing

An operator reprocesses a completed job. The new attempt identifies its trigger and versions, preserves prior outcomes, and explicitly indicates whether it supersedes the previous accepted result.

### Scenario O: Prompt injection

A posting instructs an agent to reveal secrets or ignore its task. The extractor treats the instruction as untrusted posting content and does not follow it.

## Product decision status

The v1 disposition is recorded below. D1–D15 refer to `spike/job-pipeline/00-overview.md`; follow-ups are recorded in `07-followups.md`.

Eligibility items 1–3 are resolved by D4, D5, and the versioned `job_eligibility` policy. Item 4 is resolved by D8: unknown is terminal unless explicitly reprocessed.

Lifecycle items 5–7 are resolved by D8 and the snapshot work rule. Item 8 is resolved by explicit reprocessing and the version rule: versions do not trigger automatic work.

Identity items 9–10 use the measured URL key, requisition identifier, and same-company fingerprint rules. Items 11–12 are deferred by D15 to cross-company duplicate detection and a future slow `recheck` command.

Contract items 13–16 are resolved by the field-state schema, accepted eligible-result validation, D6 vocabularies, and multi-valued job functions. Item 17 keeps compensation as grounded text; structured compensation is deferred. Item 18 omits principal technologies. Item 19 retains source facts without arbitrary collection caps.

Retention item 20 uses durable attempt, decision, result, and evidence history while complete raw-page retention remains outside v1. Item 21 uses the component thresholds in Phases 3 and 4. Item 22 remains an operational measurement rather than a fabricated budget. Item 23 defers consumer-specific read models and normalization additions until a consumer exists.

### Eligibility

1. What exact predicates define eligibility for a person working from Costa Rica?
2. How do work location, residency, work authorization, remote restrictions, and timezone requirements interact?
3. What precedence applies to role-specific text, general employer text, structured fields, and contradictory statements?
4. Does unknown eligibility require human review, explicit reprocessing, or limited automatic retry?

### Lifecycle and retry

5. Which unavailable conditions are terminal, and which are retryable?
6. Which technical failures are retryable, with what limits, delays, and exhaustion behavior?
7. Does retryable work remain owed after its URL disappears from the current discovery snapshot?
8. Can a version change trigger automatic reprocessing, or is reprocessing always explicit?

### Logical identity

9. Which URL parameters are non-identifying, and how are redirects evaluated?
10. Which provider requisition identifiers are authoritative?
11. How should staffing partners, mirrors, and cross-company duplicates be represented?
12. What evidence is sufficient to distinguish URL reuse from an updated existing posting?

### Eligible-job contract

13. Which fields and field states are mandatory for completed enrichment?
14. Are accepted partial results useful, or must incomplete jobs remain retryable?
15. Which controlled vocabularies are required for location, work mode, employment type, experience level, and job function?
16. May jobs have multiple locations, work modes, employment types, or job functions?
17. Should compensation, education, certifications, languages, work authorization, relocation, travel, department, requisition ID, posting date, and closing date be first-class fields?
18. Are principal technologies required, and what downstream purpose do they serve?
19. Are any field or collection maximums required by downstream consumers?

### Retention, quality, and operation

20. How long must source content, evidence, attempts, and extracted results be retained?
21. What precision, recall, and human-review thresholds are acceptable for each extracted component?
22. What daily volume, completion time, source-rate, and variable-cost budgets apply?
23. Which downstream search, matching, analytics, or user experiences define normalization needs?

Database placement, package boundaries, libraries, and model defaults are resolved for v1 by D1–D14. Deployment topology remains outside the current implementation scope.

## Legacy prompt disposition

| Legacy rule | Disposition |
|---|---|
| Stage 1 direct job URL discovery | Superseded by `job-discovery`. |
| Stage 1 title extraction | Retained as eligible-job metadata. |
| Costa Rica, LATAM, and global eligibility rules | Retained as candidate policy pending clarification. |
| Default unknown location to San José | Rejected because it fabricates a fact. |
| Infer remote or onsite solely from location breadth | Rejected without supporting evidence. |
| Controlled metadata vocabularies | Open pending downstream requirements. |
| Experience-level year thresholds | Open; overlapping legacy ranges require correction. |
| Responsibilities, required skills, preferred skills, and benefits | Retained as separate concepts. |
| Generate missing responsibilities | Rejected as fabricated content. |
| Require five items per collection | Rejected because sparse sources are valid. |
| Limit every collection to ten items | Open pending downstream need. |
| Preserve source wording | Retained through provenance. |
| Capitalize or rewrite source statements | Not required; normalized presentation must not replace source wording. |
| Technology alias normalization | Retained in principle; canonical vocabulary remains open. |
| Remove technology versions or collapse product families | Open because this can discard meaningful requirements. |
| Required technology wins over preferred duplicate | Retained while preserving evidence for both mentions. |
| Principal technologies | Open pending a defined downstream purpose. |
| Separate prompt stages over raw HTML | Not required. |

## Requirements completion criteria

This phase is complete when:

1. Eligibility predicates and precedence are approved.
2. Unknown, unavailable, retryable, exhausted, interrupted, and completed behavior is unambiguous.
3. Logical identity expectations cover URL variants, redirects, requisition IDs, mirrors, and URL reuse.
4. Mandatory eligible-job fields and accepted partial-result behavior are approved.
5. Retention and provenance expectations are measurable.
6. Component-level quality thresholds and operating budgets are defined.
7. Stakeholders agree that the document describes desired outcomes without selecting the implementation.
