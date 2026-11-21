# Geographic eligibility review — Costa Rica — operator-reviewed v1

Collected: 2026-11-01 UTC. Scope: eight companies, four postings each, 32 postings total. **All 32 labels are human-accepted in the user conversation: 29 unchanged and three overridden (GOL01, KAK01, KAK02 → eligible).** Approval recorded: 2026-11-02 UTC, using the runtime clock; this is the recording date, not an invented conversation timestamp or employer confirmation.

The question is whether to retain an opportunity for a candidate who intends to remain based in Costa Rica. The operator-approved opportunity-screening policy is deliberately broader than legally confirmed hiring eligibility. Eligible means retain for follow-up, not employer approval, confirmed country payroll, work authorization, visa eligibility, schedule feasibility, skill fit, benefits entitlement, or verified vacancy availability.

## SYS-2028 amendments

These amendments postdate the v1 review below and supersede the statement that no case is authorized as a prompt example.

- **Prompt examples.** By user decision in SYS-2028, six development cases back anonymized, minimal prompt examples for the one-shot eligibility contract (`opportunity-oneshot-v1`): GOL01 (bare remote with no exclusion), GOL03 (remote limited to foreign-only alternatives), JOB02 (foreign base and foreign authorization; travel contextual), NBS03 (broad LATAM permission conflicting with a concrete foreign location), OCE04 (ambiguous location code with a hybrid/remote conflict), and JOB03 (timezone requirement without residence permission). The prompt carries trimmed excerpts only: no case IDs, company names, job titles, source URLs, full postings, or rationales. Because the model has seen these patterns, the six cases are excluded from the primary development score and reported only in a secondary, contaminated score. No exposed-regression case was used to build the prompt.
- **Synthetic examples (v2).** After the v1 development evaluation, the user approved replacing those six corpus-derived examples with synthetic examples of the same patterns (`opportunity-oneshot-v2`). No corpus case backs a v2 example, and no policy phrase quotes a posting, so the six cases above returned to the primary development score.
- **Fact expectations removed.** The 24 SYS-2027 reviewed atomic-fact files were deleted by explicit user decision; the corpus manifest records their pre-removal corpus and manifest hashes. Posting texts, reviewed eligibility labels, provenance, and the development/exposed-regression split are unchanged.
- **Evidence obligations.** Development cases now carry drafted supporting/conflicting evidence obligations derived from the reviewed `must_cite` quotes. They were reviewed by the operator on 2026-11-15 and are evaluation truth for the development cases only.
- **v2 label change.** The tracked v2 corpus records OCE03 as unknown (a foreign header without restrictive wording is unresolved), superseding the v1 ineligible label shown in the tables below.

## Operator-approved opportunity-screening policy v1

Explicit Costa Rica, broad LATAM/Latin America, Americas, or worldwide/“anywhere” role geography includes Costa Rica unless concrete wording conflicts. Remote wording, including “100% Remote,” is sufficient to retain an opportunity when no explicit excluding restriction is stated (GOL01). A LATAM timezone target together with remote work is sufficient for opportunity screening (KAK02); timezone overlap alone is not residence permission or a legal hiring guarantee. “Work globally” in the role's benefits together with Fully Remote is sufficient (KAK01); the community's country count alone is not proof of hiring permission.

Specific role restrictions, mandatory foreign residence, and required foreign on-site attendance override generic remote benefits. Conflicting concrete geography remains unknown: GOL04 retains LATAM versus South America, NBS03 retains Bogotá versus LATAM, and OCE04 retains Hybrid/CO versus Fully remote. CO remains unexpanded and is not treated as Costa Rica. Missing role information remains unknown (JOB03/JOB04); separately captured company-wide remote/Americas marketing is not silently inherited by these accepted cases.

Preferences are not restrictions: BAJ03/NBS02 remain eligible, and BAJ04's LATAM role title counts. Client countries, market experience, currency, English fluency, and employer addresses are not candidate residence requirements. Schedule feasibility and occasional travel require separate follow-up; no relocation or travel exception is inferred.

## Policy resolutions and retained case-specific limits

The user accepted all other 29 existing labels without relabeling. GOL03 and OCE03 therefore retain ineligible even though their foreign role locations do not literally say “only”; OCE03's US header may still describe sales territory. These are accepted case-specific judgments, not a new universal rule that every named foreign header excludes Costa Rica. General precedence for ambiguous foreign headers, sales territories, or future company-wide benefits remains unresolved beyond these accepted cases.

GOL04 remains unknown; no LATAM-over-South-America precedence was approved. KAK01's benefits and KAK02's remote/timezone combination are resolved for opportunity screening, not legal eligibility. JOB03/JOB04 remain unknown rather than inheriting board context, and JOB02's explicit Mexico base still excludes. BAJ04's title permission and BAJ03/NBS02's preferences are accepted. The uncertainty notes below retain employer-verification limits even where the screening label is now human-accepted.

Operator policy resolution: human acceptance from the user conversation, recorded 2026-11-02 UTC; 29 unchanged labels and three explicit unknown-to-eligible overrides. No reviewer identity, exact approval time, or employer confirmation is inferred.

## Provenance and limits

This document assembles these existing artifacts from `/var/folders/dd/mk4xw0sn3rx5g46qmn1t2k7m0000gn/T/opencode/`: `review-golabs-baja.json`, `review-akurey-kake.json`, `review-nbs-jobsity.json`, and `review-railway-oceans.json`. Their source URLs, UTC capture timestamps, full captured text, and collection caveats are retained. Original E/I/U artifact labels are expanded to eligible/ineligible/unknown; the user accepted 29 unchanged labels and overrode GOL01, KAK01, and KAK02 from unknown to eligible. Per-entry operator answers now record that human acceptance, not fresh employer verification. No source artifact is modified.

Full posting text below means the entire readable text supplied in each artifact, reproduced in fenced text blocks without further omission. It is not a claim of byte-for-byte original-webpage capture. RecruitCRM supplies parent-page visible text separately from its description-bearing about:blank iframe; both are included. Akurey/Kake artifacts are readable Markdown transcriptions with navigation/contact/cookie material removed during collection; one source had decorative glyphs removed. Nearshore preserves page chrome; Jobsity preserves the complete detail panel, not unrelated sidebar listings or the application iframe. Railway/Oceans captures had navigation, footer, cookie UI, unrelated cards, and application-form fields removed during collection. Existing retained boilerplate and unfilled Kake screening questions are preserved; they contain no applicant answers.

The artifacts report public-page Playwright collection, no external LLM/model API calls, no pipeline processing, no database operations, no .env reads, and no application submissions. Jobsity's initial page loads may have automatically requested application iframes; later captures blocked them. Strict zero application-form network access therefore cannot be claimed, although no candidate data was entered and no form was submitted. This assembly does not revisit live sites or run the pipeline.

All selected records had readable detail text and live-board/application affordance evidence. Vacancy labels below are observations at capture, not independently confirmed funded headcount, working application backends, or current recruiter activity. Publication/closing dates were not supplied as verified dates; they are not inferred from URL IDs, “New,” or contract duration. The January handover in BAJ02 has no year. No talent pools are included: Jobsity “Live Receptionist”/“Always Hiring” and Nearshore's generic “Submit your application” invitation were excluded.

These 32 texts and labels were already seen during preparation; they are **not an untouched held-out evaluation set**. The accepted v1 expectations and deterministic split are frozen in `output/eligibility-replay/opportunity-screening-v1/cases.json` and `manifest.json`. Development has 24 cases (three per company); reserved test has eight (one per company), with eligible/ineligible/unknown coverage in both. All three overrides are development cases. No case is authorized as a prompt example; test cases and labels must not be read for future tuning or model selection. Fresh external unseen postings are needed for a genuinely blind holdout. No benchmark scores, paid model comparison, rollout approval, or production-policy/default changes are established here.

Reserved test IDs: AKU02, BAJ01, GOL04, JOB04, KAK04, NBS01, OCE03, RAI04 (4 eligible, 2 ineligible, 2 unknown). Development IDs: GOL01, GOL02, GOL03, BAJ02, BAJ03, BAJ04, AKU01, AKU03, AKU04, KAK01, KAK02, KAK03, NBS02, NBS03, NBS04, JOB01, JOB02, JOB03, RAI01, RAI02, RAI03, OCE01, OCE02, OCE04 (15 eligible, 6 ineligible, 3 unknown).

Split rule: sort company names lexicographically; within each company exclude the three overrides from test candidates and rank remaining IDs by ascending SHA-256 of UTF-8 `opportunity-screening-v1:<ID>`. Enumerate the Cartesian product in that order and take the first tuple with test label counts 4 eligible, 2 ineligible, 2 unknown. All remaining cases are development. Stratification used accepted labels, not model outputs. The manifest records immutable source, content, case, split, and cases-file hashes; future changes require a new version, not overwriting v1.

Existing dependency CVE findings are recorded separately in `CVE_MITIGATION_LOG.md`; no dependency remediation or new vulnerability audit is claimed by this documentation-only assembly. Dependencies, code, database, and pipeline state are unchanged.

## Summary

| Company | Eligible (accepted) | Ineligible (accepted) | Unknown (accepted) | Total | Operator accepted |
| --- | ---: | ---: | ---: | ---: | ---: |
| Golabs Tech | 1 | 2 | 1 | 4 | 4 |
| Baja Nearshore | 3 | 1 | 0 | 4 | 4 |
| Akurey | 4 | 0 | 0 | 4 | 4 |
| Kake | 2 | 2 | 0 | 4 | 4 |
| Nearshore Business Solutions | 2 | 1 | 1 | 4 | 4 |
| Jobsity | 1 | 1 | 2 | 4 | 4 |
| Railway | 4 | 0 | 0 | 4 | 4 |
| Oceans Code Experts | 2 | 1 | 1 | 4 | 4 |
| **Total** | **19** | **8** | **5** | **32** | **32** |

| ID | Company | Title | Accepted decision | Source |
| --- | --- | --- | --- | --- |
| [GOL01](#gol01) | Golabs Tech | Senior DevOps / Site Reliability Engineer | eligible | [Posting](https://recruitcrm.io/apply/17913849976820117843vwE?source=Jobspage&new=yes) |
| [GOL02](#gol02) | Golabs Tech | OCM Consultant | ineligible | [Posting](https://recruitcrm.io/apply/17920167048160117843AVo?source=Jobspage&new=yes) |
| [GOL03](#gol03) | Golabs Tech | Recruiting Coordinator | ineligible | [Posting](https://recruitcrm.io/apply/17909601610780117843vBS?source=Jobspage&new=yes) |
| [GOL04](#gol04) | Golabs Tech | AI Engineering Manager | unknown | [Posting](https://recruitcrm.io/apply/17925326109480117843lFf?source=Jobspage&new=yes) |
| [BAJ01](#baj01) | Baja Nearshore | (AN06AM) Account Manager. Remote - LATAM | eligible | [Posting](https://recruitcrm.io/apply/17822540967650106969Ndp?source=Jobspage&new=yes) |
| [BAJ02](#baj02) | Baja Nearshore | Renewal Specialist - Remote, Mexico | ineligible | [Posting](https://recruitcrm.io/apply/17925079071980148048Iqh?source=Jobspage&new=yes) |
| [BAJ03](#baj03) | Baja Nearshore | (OP02BDR) Business Development Representative | eligible | [Posting](https://recruitcrm.io/apply/17919984197620127351tHv?source=Jobspage&new=yes) |
| [BAJ04](#baj04) | Baja Nearshore | (SD01TH) Shopify Designer - LATAM 100% REMOTO | eligible | [Posting](https://recruitcrm.io/apply/17884566834530148048PgI?source=Jobspage&new=yes) |
| [AKU01](#aku01) | Akurey | AI Systems Engineer / Agentic Engineer | eligible | [Posting](https://www.akurey.com/careers/requirements?pId=307) |
| [AKU02](#aku02) | Akurey | Marketo/Adobe Specialist | eligible | [Posting](https://www.akurey.com/careers/requirements?pId=309) |
| [AKU03](#aku03) | Akurey | Business Data Analyst (AI-Assisted Analytics) | eligible | [Posting](https://www.akurey.com/careers/requirements?pId=304) |
| [AKU04](#aku04) | Akurey | Senior Database Engineer / DBA | eligible | [Posting](https://www.akurey.com/careers/requirements?pId=308) |
| [KAK01](#kak01) | Kake | Senior AI/LLM Engineer (Python) | eligible | [Posting](https://kake.co/jobs/senior-ai-llm-engineer-python-56a4536c4a20) |
| [KAK02](#kak02) | Kake | Senior Product Data Scientist | eligible | [Posting](https://kake.co/jobs/senior-product-data-scientist-98b35d2c5ff6) |
| [KAK03](#kak03) | Kake | Senior Backend (Go) Engineer (Mexico Only) | ineligible | [Posting](https://kake.co/jobs/senior-backend-go-engineer-mexico-only-8f085dfa24f3) |
| [KAK04](#kak04) | Kake | Finance & Accounting Manager (Operations + FP&A) | ineligible | [Posting](https://kake.co/jobs/finance-accounting-manager-operations-fp-a-eae5a7b6f30b) |
| [NBS01](#nbs01) | Nearshore Business Solutions | Senior Freight Accountant – US Logistics | eligible | [Posting](https://www.careers-page.com/nearshore-business-solutions/job/RYWV9V5R) |
| [NBS02](#nbs02) | Nearshore Business Solutions | Product Manager | eligible | [Posting](https://www.careers-page.com/nearshore-business-solutions/job/7X7X93X3) |
| [NBS03](#nbs03) | Nearshore Business Solutions | Product Manager, Self Service Enablement | unknown | [Posting](https://www.careers-page.com/nearshore-business-solutions/job/Y6733X65) |
| [NBS04](#nbs04) | Nearshore Business Solutions | Field Service Engineer – Semiconductor Equipment | ineligible | [Posting](https://www.careers-page.com/nearshore-business-solutions/job/V6767X36) |
| [JOB01](#job01) | Jobsity | Sales Development Representative, LATAM | eligible | [Posting](https://careers.jobsity.com/opportunities/sales-development-representative-latam) |
| [JOB02](#job02) | Jobsity | Senior PLC Engineer | ineligible | [Posting](https://careers.jobsity.com/opportunities/senior-plc-engineer) |
| [JOB03](#job03) | Jobsity | Mid-level Android Engineer | unknown | [Posting](https://careers.jobsity.com/opportunities/mid-level-android-engineer) |
| [JOB04](#job04) | Jobsity | Configuration Specialist | unknown | [Posting](https://careers.jobsity.com/opportunities/configuration-specialist) |
| [RAI01](#rai01) | Railway | Senior Infra Engineer: Platform | eligible | [Posting](https://railway.com/careers/infra-platform) |
| [RAI02](#rai02) | Railway | Senior Product Designer | eligible | [Posting](https://railway.com/careers/Product-designer) |
| [RAI03](#rai03) | Railway | Account Executive, Scale | eligible | [Posting](https://railway.com/careers/account-executive-scale) |
| [RAI04](#rai04) | Railway | Senior Product Marketer | eligible | [Posting](https://railway.com/careers/product-marketer) |
| [OCE01](#oce01) | Oceans Code Experts | YR-15 Data Engineer | eligible | [Posting](https://www.oceanscode.com/careers/yr-15-data-engineer) |
| [OCE02](#oce02) | Oceans Code Experts | PH-25 Senior Jira Administrator | eligible | [Posting](https://www.oceanscode.com/careers/ph-25-senior-jira-administrator) |
| [OCE03](#oce03) | Oceans Code Experts | OC-29 Sales Representative | ineligible | [Posting](https://www.oceanscode.com/careers/oc-29-sales-representative) |
| [OCE04](#oce04) | Oceans Code Experts | Ax-01 Program Management Lead | unknown | [Posting](https://www.oceanscode.com/careers/ax-01-program-management-lead) |

## Golabs Tech

Source artifact: `review-golabs-baja.json`. Board: <https://recruitcrm.io/jobs/Golabs_Tech_jobs>. The capture observed 20 distinct live listing links and selected four named roles, not talent pools. For GOL01–GOL04, acquisition succeeded with HTTP 200; each source vacancy status is `apparently_open`, based on a live listing, readable description, and visible Apply To This Job control with no observed closure notice. Publication date: not shown (`null`); independently confirmed vacancy status: not obtained.

### GOL01

**Senior DevOps / Site Reliability Engineer**

Source: <https://recruitcrm.io/apply/17913849976820117843vwE?source=Jobspage&new=yes>. Fetched UTC: `2026-11-01T20:48:58.540655+00:00`. Location: Remote; no explicit hiring country or region. Review status: `operator_reviewed_v1`.

**Operator answer: eligible.** Human override from unknown under opportunity-screening v1: “Work arrangement: Remote” and “100% Remote.” suffice to retain this opportunity absent an explicit excluding restriction. This does not establish legally confirmed Costa Rica hiring; the employer's board, USD compensation, and country-holiday benefits are not the basis.

Exact evidence from the posting:

```text
Work arrangement: Remote
```

```text
100% Remote.
```

```text
Local Holidays: All official holidays in your country.
```

**Remaining verification limits:** no permitted hiring-country list is stated. Ask whether Costa Rica-based contractors are accepted; “your country” is not an explicit worldwide allowance. The screening label is accepted, not employer-confirmed.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full parent-page visible text:

```text
More Jobs
Senior DevOps / Site Reliability Engineer
Apply To This Job
Close
```

Full posting text — description iframe (`about:blank`):

```text
Senior DevOps / Site Reliability Engineer

Type: Contractor, Full-time
Work arrangement: Remote

About the Role

We are looking for a Senior DevOps / Site Reliability Engineer to own the reliability, delivery pipeline, and AWS infrastructure behind a real-money skill gaming platform. Your first priority will be completing the move from manual Jenkins deployments to automated releases on GitHub Actions and Terraform-managed ECS. This is a hands-on senior role on a small team with real ownership.


Responsibilities

Own the GitHub Actions and Terraform ECS deployment pipeline, including approvals, migration gates, and rollback.Make zero-downtime database migrations the default for a PostgreSQL-backed Django system that handles payments.

Manage the Terraform estate and a multi-account AWS Organization, bringing all resources under code.

Rebuild alerting around SLOs, own observability tooling, and lead incident response and postmortems.

Own secrets, certificates, patching, and security reviews required by gaming and payments partners.

Optimize AWS spend and report on it monthly.

Extend existing AI agents to automate operational toil, and document runbooks for the team.


Tech Stack

AWS (ECS, RDS, Redshift, GameLift)

Terraform / Terraform Cloud

GitHub Actions / Jenkins

Docker

Python / Django

PostgreSQL

Grafana, Sentry, PagerDuty

What We’re Looking For​​​​

5+ years of hands-on experience in DevOps or SRE roles, running production workloads on AWS and owning the Terraform behind them.

Proven experience automating a manual release process.

Background in systems where downtime has financial or regulatory impact (payments, fintech, gaming, or similar).

Enough Python and Django knowledge to understand how migrations and workers affect deployments.

Practical experience defining SLOs, reducing alert noise, and building useful dashboards.

Strong written and verbal English communication skills.

Significant professional experience using generative AI tools (e.g., Claude Code) in coding and operations, in addition to a strong DevOps skillset.

Nice to Have

Advanced GitHub Actions (reusable workflows, OIDC to AWS).

Blue/green or canary deployments.

Jenkins to GitHub Actions migration experience.

Unity or mobile game build pipelines.

Regulated real-money products (KYC, geolocation).

FinOps experience with measurable cost reductions.

Why join us?

Competitive compensation paid in USD.

100% Remote.

Paid Time Off: 12 PTO days per year.

Local Holidays: All official holidays in your country.

Birthday Off.

Career Growth opportunities.

Recognition Program.

Paid approved leaves.
```

### GOL02

**OCM Consultant**

Source: <https://recruitcrm.io/apply/17920167048160117843AVo?source=Jobspage&new=yes>. Fetched UTC: `2026-11-01T20:49:07.101223+00:00`. Location: Hybrid – Mexicali, Mexico (3 days onsite, 2 days remote). Review status: `operator_reviewed_v1`.

**Operator answer: ineligible.** Role requires recurring onsite work in Mexicali, Mexico, three days each week. No broader location permission allows performing the role from Costa Rica.

Exact evidence from the posting:

```text
Location: Hybrid – Mexicali, Mexico (3 days onsite, 2 days remote)
```

```text
Ability and willingness to work onsite in Mexicali, Mexico, three days per week, with two days working remotely.
```

**Why uncertainty remains:** no exception or relocation arrangement is confirmed. The suggestion assumes remaining Costa Rica-based, not relocating to satisfy Mexico attendance.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full parent-page visible text:

```text
More Jobs
OCM Consultant

Mexicali

Apply To This Job
Close
```

Full posting text — description iframe (`about:blank`):

```text
OCM Consultant

Location: Hybrid – Mexicali, Mexico (3 days onsite, 2 days remote)
Engagement: Contractor · 12+ months
 

About the Role

We are looking for an OCM (Organizational Change Management) Consultant to support organizational change initiatives for a leading enterprise client in the manufacturing industry.

The ideal candidate has experience in OCM and SAP or S/4HANA environments, understands manufacturing operations, and has strong communication skills in both English and Spanish. This role will focus on stakeholder engagement, communications, roadshows, and translating change management materials from English to Spanish.

The position requires onsite collaboration in Mexicali, Mexico, particularly during key project milestones. While the role will involve a hybrid work arrangement, candidates must be able to work onsite as required.

Responsibilities

Coordinate onsite OCM activities, including stakeholder identification, roadshows, communications, and translations.

Support stakeholder engagement and change management communication initiatives.

Translate OCM materials from English to Spanish, ensuring accuracy and cultural relevance.

Collaborate with key stakeholders, supervisors, and onsite teams to support change adoption.

Assist with the execution of organizational change management activities in an SAP or S/4HANA environment.

Support project milestones and participate in onsite activities as needed.

Requirements

Experience in Organizational Change Management (OCM), ideally at the Consultant or Senior Consultant level.

Hands-on experience with OCM initiatives in SAP or S/4HANA environments.

Understanding of manufacturing operations and related business processes.

Bilingual English and Spanish proficiency (must-have), including reading, writing, speaking, and translating materials from English to Spanish.

Excellent verbal and written communication skills.

Ability and willingness to work onsite in Mexicali, Mexico, three days per week, with two days working remotely.

Flexibility to travel to the client site during critical project milestones, when required.

 
```

### GOL03

**Recruiting Coordinator**

Source: <https://recruitcrm.io/apply/17909601610780117843vBS?source=Jobspage&new=yes>. Fetched UTC: `2026-11-01T20:49:15.644090+00:00`. Location: Remote in Argentina or Mexico. Review status: `operator_reviewed_v1`.

**Operator answer: ineligible.** Role-specific location names Argentina or Mexico, not Costa Rica or broad LATAM. Generic 100% remote and local-holiday benefits do not broaden this role-specific location.

Exact evidence from the posting:

```text
Contractor · Remote in Argentina or Mexico · Full-time · 9-month project
```

```text
100% Remote.
```

```text
Local Holidays: All official holidays in your country.
```

**Why uncertainty remains:** whether the employer permits exceptions is unverified. ET/PT coverage and Belfast/Lisbon hiring hubs do not change the stated candidate location.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full parent-page visible text:

```text
More Jobs
Recruiting Coordinator
Apply To This Job
Close
```

Full posting text — description iframe (`about:blank`):

```text
Recruiting Coordinator
Contractor · Remote in Argentina or Mexico · Full-time · 9-month project

We are looking for a highly organized and detail-oriented Recruiting Coordinator to support our client's Talent Acquisition and People Operations teams. This role will focus on coordinating interviews, managing candidate communications, preparing offer letters, and ensuring a smooth and professional candidate experience throughout the hiring process.

You will join a fast-moving, global Talent Acquisition team that hires across a wide range of functions, with a strong current focus on AI engineering and go-to-market sales roles. Most interviews currently take place across the client's hiring hubs in Belfast and Lisbon.

This is a dedicated support role: you will not be involved in sourcing, screening, or compensation negotiation. Instead, you will act as a primary scheduler and offer letter processor, freeing up the core team to focus on implementing a new interview scheduling platform.
Responsibilities
Coordinate and schedule candidate interviews across multiple calendars, recruiters, hiring managers, and interview panels, providing coverage for Eastern (ET) and Pacific (PT) time zones.
Schedule interviews using Workday Recruiting and Outlook, following the team’s well-documented processes.
Work from a shared, centralized scheduling inbox on a first-in, first-out basis, proactively picking up new requests in your time zone (following the team’s color-coding system) without waiting to be assigned.
Handle scheduling requests of varying complexity (low, medium, and high effort): from standardized, high-volume roles such as AI engineering, sales, finance, marketing, and backfills, to executive searches (VP, Senior Director) that require aligning calendars with C-level stakeholders and their executive assistants.
Manage end-to-end interview logistics, including calendar invitations, video conferencing links, room bookings, and rescheduling.
Serve as the primary point of contact for candidates regarding interview logistics, ensuring timely and professional communication.
Prepare and issue offer letters based on approved compensation packages and employment terms.
Review offer letters prepopulated in Workday templates against the recruiter’s requisition, verify that all compensation components (base salary, bonus, sign-on bonus, etc.) are accurate, and submit them for final audit. Compensation negotiation is handled entirely by the recruiters.
Track offer letter progress, follow up on pending signatures, and maintain accurate records in the ATS and/or HRIS.
Keep interview schedules, candidate information, and offer statuses up to date across internal systems.
Provide regular updates to the recruiting team and proactively identify scheduling conflicts, delays, and potential blockers.
Support recruiters across multiple time zones, including providing hands-on guidance when needed, and remain available and responsive on the team’s Microsoft Teams channel during committed working hours.
Adopt the new interview scheduling software once the team rolls it out.
Support additional recruiting coordination and People Operations activities as needed.
Requirements
Previous experience in Recruiting Coordination, Talent Acquisition Operations, HR Administration, or a similar role.
Experience coordinating interviews across multiple stakeholders and time zones.
Experience coordinating with senior leaders and executive assistants for executive-level interview processes.
Familiarity with applicant tracking systems (ATS), HRIS platforms, and calendar management tools.
Strong proficiency with Outlook calendar and email management.
Strong organizational skills and attention to detail, particularly when managing multiple priorities.
Excellent written and verbal communication skills, with a candidate-focused approach.
Ability to handle confidential information with discretion and professionalism.
Proactive problem-solving skills and the ability to work independently in a fast-paced environment.
Self-starter with a “roll up your sleeves” attitude: comfortable jumping into the shared inbox, handling frequent requests from recruiters, and collaborating closely with the team.
Flexible and reliable, with the ability to commit to set working hours aligned with Eastern or Pacific time.
Advanced English proficiency (B2+/C1). All communication and operations are in English; no other languages are required.
Nice to have
Hands-on experience with Workday Recruiting, especially for interview scheduling and offer letters (highly valued).
Experience processing offer letters within an ATS or HRIS.
General HR knowledge, as the role may later include answering HR-related questions.
Experience supporting both high-volume and executive-level hiring in a global organization.
Experience with dedicated interview scheduling software.
Hiring process
Interview with our recruiting team.
Short initial screen (10–15 minutes) with the hiring manager.
Interview with two members of the client’s team, including a skills assessment.
Interview with the client’s Recruiting Manager.
Background check.
Client interviews are scheduled back-to-back on the same day whenever possible to keep the process quick.

Why join us?
Competitive compensation paid in USD.
100% Remote.
Paid Time Off: 12 PTO days per year.
Local Holidays: All official holidays in your country.
Birthday Off.
Career Growth opportunities.
Recognition Program.
Paid approved leaves
```

### GOL04

**AI Engineering Manager**

Source: <https://recruitcrm.io/apply/17925326109480117843lFf?source=Jobspage&new=yes>. Fetched UTC: `2026-11-01T20:49:24.143408+00:00`. Description title: AI Engineering Manager – AI Patient Scheduling. Location: Remote in Latin America; conflicting benefits text says South America. Review status: `operator_reviewed_v1`.

**Operator answer: unknown.** The role header permits Latin America, which includes Costa Rica, but the offer explicitly says work from anywhere in South America, which does not. This is a concrete conflicting geographic statement rather than merely generic remote benefits. The user accepted unknown; no role-header precedence or finding that the South America line is inapplicable boilerplate was approved.

Exact evidence from the posting:

```text
Contractor · Full Time · Remote in Latin America · CST & IST overlap · Professional English fluency
```

```text
100% Remote Work — Work from anywhere in South America.
```

**Why uncertainty remains:** the artifact does not establish that the narrower offer is inapplicable boilerplate. CST/IST overlap is separate from residence permission; the distributed team's locations are not a hiring-country list.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full parent-page visible text:

```text
More Jobs
AI Engineering Manager
Apply To This Job
Close
```

Full posting text — description iframe (`about:blank`):

```text
AI Engineering Manager – AI Patient Scheduling

Contractor · Full Time · Remote in Latin America · CST & IST overlap · Professional English fluency

Our client is a healthcare AI company building AI-driven patient access and engagement products for enterprise health systems, with Voice AI and consumer AI agents live in production and integrated with major EHRs such as Epic, Oracle Health (Cerner), and Athena.

We are looking for a hands-on AI Engineering Manager to lead the team behind their self-service AI scheduling product, where patients book care through Voice AI agents, consumer AI agents, or an RCS chat interface. You will size and sequence the work, review designs and code, remove cross-team blockers, and drive roadmap commitments to production against customer dates.

Responsibilities
Platform ownership:
Lead delivery across the scheduling platform: a Python/FastAPI service with REST and MCP interfaces, a LangGraph-based workflow runtime, OCR and LLM document ingestion with human-in-the-loop routing, and the MCP tools AI agents call during live patient interactions.
Coordinate dependencies with the EHR integration (FHIR R4, Epic/Cerner/Athena), contact-center, MCP server, and Infra/Platform teams.
Delivery ownership:
Own roadmap items, epics, and release commitments; turn them into sequenced, sized work with clear acceptance criteria and drive them to production.
Run the delivery cadence (planning, prioritization, status, risk escalation) and re-plan honestly when scope or dependencies move.
Hands-on technical leadership:
Review designs and code across the platform deeply enough to catch the decisions that cost the team later.
Lead the technical response on production issues and customer escalations, and make sure root causes are fixed in the system.
Cross-team coordination:
Own the dependency map: what you are waiting on, who owns it, and the committed date.
Negotiate interface contracts early and escalate blocked dependencies quickly and specifically.
Team leadership:
Lead a small, senior team distributed across South America, North America, and India: set expectations, give direct feedback, and build shared working norms.
Assess capacity against the roadmap, flag gaps early, and onboard new engineers quickly into a large monorepo.
Agentic SDLC adoption:
Lead the team's move from a Cursor-based, copilot-assisted workflow to agent-driven delivery across planning, implementation, review, and testing.
Hold agent-produced code to the same review, testing, and observability standards as hand-written code, and report on the pilot with evidence.
Requirements
7+ years in software engineering, with 2–3+ years managing or leading engineering teams, including distributed teams across time zones.
Track record of owning delivery for a product or platform area end to end, from roadmap through production, including what went wrong.
Current hands-on engineering ability: this role reviews code and makes technical calls, and is not a purely process-facing management role.
Experience coordinating across multiple engineering teams with separate ownership and cadences.
Strong backend background: Python and/or TypeScript, REST API design, relational data modeling, caching, and containerized services.
Working understanding of LLM application patterns: agent orchestration, workflow graphs, tool/function calling, MCP, prompt and context design, and evaluation.
Comfort with modern delivery infrastructure (Git-based workflow with code review, CI/CD, Docker, cloud deployment, observability tooling) and AI-assisted development tools in daily use.
Ability to read an unfamiliar large codebase and form an accurate picture of it quickly.
Professional fluency in English and the written communication skills to run cross-team coordination.
Nice to Have
Healthcare systems experience, particularly EHR integration (Epic, Oracle Health/Cerner, Athena) or FHIR.
Voice AI, conversational AI, or contact-center platform experience.
Document extraction / IDP pipelines, including human-in-the-loop review design.
Multi-tenant SaaS with per-tenant isolation and configuration.
Familiarity with HIPAA and PHI handling requirements in production systems.
Experience leading a team through the adoption of AI-assisted or agentic development practices.
Additional Information
Reports to the Director of Engineering (North America).
Requires working overlap with both CST and IST time zones.
Interview process: a technical deep-dive on systems you built or led, a delivery-leadership discussion, a dependency-management and prioritization session, and a hands-on technical exercise.
What We Offer
Competitive USD Compensation — Get paid in USD.
100% Remote Work — Work from anywhere in South America.
Equipment Provided — We provide the equipment you need to do your best work.
Paid Time Off — Enjoy 12 PTO days per year to rest, recharge, and take time for yourself.
Local Public Holidays — Celebrate all official holidays in your country.
Birthday Off — Enjoy your special day, on us!
Career Growth Opportunities — Keep learning, developing your skills, and advancing your career.
Recognition Program — Your contributions matter, and we make an effort to celebrate them.
Paid Approved Leaves — Receive paid leave for eligible, approved absences, in accordance with company policies.
```

## Baja Nearshore

Source artifact: `review-golabs-baja.json`. Board: <https://recruitcrm.io/jobs/View%20Openings>. The capture observed 17 distinct live listing links. BAJ01–BAJ04 acquisition succeeded with HTTP 200; source vacancy status is `apparently_open`, based on live listing, readable description, and Apply To This Job control without observed closure text. Publication date: not shown (`null`). Employer confirmation: not obtained.

### BAJ01

**(AN06AM) Account Manager. Remote - LATAM**

Source: <https://recruitcrm.io/apply/17822540967650106969Ndp?source=Jobspage&new=yes>. Fetched UTC: `2026-11-01T20:49:32.603773+00:00`. Location: Remote from anywhere in Latin America. Review status: `operator_reviewed_v1`.

**Operator answer: eligible.** Explicit permission to work from anywhere in Latin America includes Costa Rica. US client coverage and PT/ET working hours are schedule/client requirements, not a US residency or work-authorization restriction.

Exact evidence from the posting:

```text
100% remote from anywhere in Latin America
```

```text
Comfortable working US hours: 9 AM – 6 PM PT or ET depending on assigned clients
```

**Why uncertainty remains:** country-specific engagement terms and the applicant's ability to meet client hours are not confirmed. The geographic suggestion does not resolve either.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full parent-page visible text:

```text
(AN06AM) Account Manager. Remote - LATAM

Share This Job

Apply To This Job
Close
```

Full posting text — description iframe (`about:blank`):

```text
About the Role
We are a fast-growing digital marketing agency specializing in serving attorneys and law firms across the United States. We are looking for two experienced Account Managers to join our team and own the client experience from onboarding through ongoing strategy. You will serve as the primary point of contact for a portfolio of legal clients, translating marketing deliverables into business results and building long-term partnerships.
What You'll Do
Manage a portfolio of approximately 20 attorney/law firm clients across the US
Serve as the main liaison between clients and internal delivery teams (SEO, PPC, Web, Social)
Lead regular client check-ins, report reviews, and strategy sessions
Translate campaign performance data into clear, actionable insights for non-technical clients
Monitor and communicate KPIs including funnel metrics, lead volume, and ROI
Identify upsell and expansion opportunities; drive client retention and satisfaction
Coordinate deliverables across internal teams using project management tools
Maintain accurate client records and communication logs in HubSpot
What We're Looking For
2+ years of experience as an Account Manager at a digital marketing agency (required)
Fully bilingual in English and Spanish (written and spoken, professional level)
Solid understanding of digital marketing services: SEO, Google Ads/PPC, Social Media Ads, and Web Development
Ability to read and explain funnel metrics, PPC performance reports, and SEO dashboards — not required to execute, but must understand the deliverables
Experience managing multiple client accounts simultaneously (15–25 range preferred)
Strong communication, organization, and relationship management skills
Comfortable working US hours: 9 AM – 6 PM PT or ET depending on assigned clients
Tools & Tech
HubSpot (CRM and client communication)
Slack (internal team collaboration)
Google Workspace (Docs, Sheets, Meet)
Microsoft Office Suite
Familiarity with marketing reporting platforms (Google Analytics, Google Ads, etc.) is a plus
Compensation & Benefits
Base salary + Performance bonus tied to client retention, upsells, and quarterly KPIs
100% remote from anywhere in Latin America
Collaborative, high-growth team environment
Opportunity to grow into a senior or team lead role as the agency scales
How to Apply
If you have agency-side account management experience and want to work with a focused, results-driven team, we want to hear from you. Apply with your resume and a brief note on your relevant experience managing digital marketing clients.
```

### BAJ02

**Renewal Specialist - Remote, Mexico**

Source: <https://recruitcrm.io/apply/17925079071980148048Iqh?source=Jobspage&new=yes>. Fetched UTC: `2026-11-01T20:49:41.102951+00:00`. Location: Remote, based in Mexico. Review status: `operator_reviewed_v1`.

**Operator answer: ineligible.** Explicitly based in Mexico, with no broader LATAM or worldwide hiring permission. Remote work and an international team do not remove the country-specific location.

Exact evidence from the posting:

```text
Remote, based in Mexico.
```

```text
Working hours aligned with US time zones
```

```text
International, collaborative environment with a growing team in Mexico
```

**Why uncertainty remains:** exceptions and Costa Rica contracting are unverified. The current specialist “stays until January” without a year; this is not proof of closure or a publication date.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full parent-page visible text:

```text
Renewal Specialist - Remote, Mexico

Share This Job

Apply To This Job
Close
```

Full posting text — description iframe (`about:blank`):

```text
​​​​​​About the Role
Renewal Specialist role in a newly created Customer Success team at a global media-technology software company with French roots and English as the working language
The company serves broadcasters, sports organizations (clubs, sports TV) and government institutions (parliaments, federal agencies) with complex, multi-channel media operations
The team is shifting from reactive support to proactive, commercial account ownership: renewals, cross-selling and recurring revenue in existing accounts, freeing Sales to focus on new markets
The current Renewal Specialist is leaving on good terms and stays until January, ensuring a structured handover

What Success Looks Like

Renewals closed on time, with churn and downsell kept to a minimum across your portfolio
Clients trust you as their go-to contact because you understand their technical setup and what is feasible
Cross-sell and upsell opportunities spotted early and brought to the right commercial owner
Accurate visibility on renewal pipeline, risks and forecast at all times

Key Responsibilities

Own the end-to-end renewal cycle for a portfolio of approximately 30 complex enterprise accounts, from early outreach through negotiation and contract signature
Understand each client’s technical environment and needs, and translate them into renewal and expansion opportunities
Lead renewal negotiations, including pricing adjustments, multi-year agreements and renewals linked to technology migrations
Identify churn risks early and coordinate mitigation with technical, Sales, Finance and Legal teams
Learn the product and services portfolio in depth to anticipate cross-selling opportunities
Maintain accurate account tracking, forecasting and pipeline updates in the CRM

Location and Environment

Remote, based in Mexico. 
Working hours aligned with US time zones
International, collaborative environment with a growing team in Mexico

Compensation

OTE of USD 55,000–60,000 per year:
USD 35,000–40,000 base (~USD 2,900–3,300/month)
USD 20,000 in commissions/bonus (~USD 1,650/month)

Ideal Candidate Profile

Around 5 years of experience managing existing B2B technology/software accounts, with ownership of renewals and/or upsell and cross-sell
Solid technical background: able to understand complex technical environments and discuss them confidently with clients (e.g., Customer Success, Technical Account Management, Solutions/Systems Engineering or Support roles with commercial exposure)
Advanced English (C1+) as the daily working language; native Spanish
Proactive, customer-oriented and commercially curious
Experience working remotely with US or international clients is a strong plus
Strong CRM and Excel skills for pipeline and forecast management
Nice to have: experience in media technology, broadcast, telecom or SaaS; French, German or other languages

Interview Process

Initial screening with our talent team, including a short video introduction in English
2 to 3 interview stages with the hiring managers, potentially including the current Renewal Specialist
Final decision by the hiring team

Important Note

This is a commercial role in a technical environment: we’re looking for someone equally comfortable discussing a client’s technical setup and negotiating their renewal
We value a proactive, advisory approach over years of experience alone
```

### BAJ03

**(OP02BDR) Business Development Representative**

Source: <https://recruitcrm.io/apply/17919984197620127351tHv?source=Jobspage&new=yes>. Fetched UTC: `2026-11-01T20:49:49.641533+00:00`. Location: Remote, Latin America (Mexico and Colombia preferred). Review status: `operator_reviewed_v1`.

**Operator answer: eligible.** Explicitly based in Latin America. Mexico/Colombia are preferred and Mexico City/Los Cabos are a plus, not mandatory residence or a country-only restriction. US business hours are a schedule requirement, not US geography.

Exact evidence from the posting:

```text
Location: Remote, Latin America (Mexico and Colombia preferred)
```

```text
Based in Latin America, with Mexico and Colombia preferred. Candidates in Mexico City or Los Cabos are a plus for occasional in-person team sessions
```

```text
Available to work US business hours
```

**Why uncertainty remains:** confirm whether occasional sessions require travel from nonpreferred countries and whether Costa Rica contracting is available. Do not turn “preferred” or “a plus” into “only.”

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full parent-page visible text:

```text
(OP02BDR) Business Development Representative

Share This Job

Apply To This Job
Close
```

Full posting text — description iframe (`about:blank`):

```text
Job Description

Business Development Representative (Cold Calling)
Location: Remote, Latin America (Mexico and Colombia preferred)
Type: 40 hours per week, US business hours

About the company
We are partnering with a US-based outbound go-to-market agency that books qualified sales meetings for B2B companies. Cold calling is at the core of their model: the vast majority of the meetings they set come from the phone. They build and run complete outbound teams for their clients, tech stack included, and they practice exactly what they sell.

The role
You will be dedicated to cold calling, working a book of 4 prospecting seats. Each seat has 60 minutes of focused calling per day, so a full book is about 4 hours of active prospecting daily. The data, dialer, and CRM are already set up for you, so your time goes where it matters most: live conversations with prospects and booking meetings that actually happen.

What you’ll do

Run focused cold calling sessions on a dialer built for speed, about 4 hours per day across your 4 seats
Book qualified meetings for each seat in your book
Log notes, outcomes, and follow-ups in the CRM
Review your own and your peers’ call recordings, and take part in coaching and knowledge sessions
Share what you hear in the market: what is landing, what objections come up, what should change
As the program grows, join monthly calibration syncs with client partners to review messaging and results

How success is measured
This team measures quality time on the phone, not dial counts.

60 minutes of active prospecting time per seat per day, using focus timer sessions (25/5 or 50/10). With a full book of 4 seats, that is 4 hours of active prospecting daily
2 qualified meetings held per seat per week as a healthy baseline
With a full book of 4 seats, that is 8 meetings per week, around 32 per month

What we’re looking for

Cold calling experience, ideally selling B2B into the US market
Proven ability to sustain several hours of live calling per day
Advanced English with a neutral to moderate accent, comfortable representing brands professionally
Self-directed and reliable in a fully remote environment
Comfortable working in CRM and dialer workflows
Experience at outbound agencies, startups, or as an independent BDR/SDR contractor is a plus
Available to work US business hours
Based in Latin America, with Mexico and Colombia preferred. Candidates in Mexico City or Los Cabos are a plus for occasional in-person team sessions

Compensation

Base salary: $2,000 to $2,500 USD per month, full-time (40 hours per week)
Uncapped commission per qualified meeting held:
$20 per qualified meeting held, with a target of 32 per month
$30 per qualified meeting held from the 33rd meeting onward
Example: 32 meetings in a month adds $640; 40 meetings adds $880

Why this role

Everything is set up for you: data, dialer, CRM, and coaching
A clear structure built around focused calling blocks, with the rest of the day for CRM work, call reviews, coaching, and breaks
Uncapped earning potential
Small team with direct access to the founders
Opportunity to be part of the company’s first LATAM team
```

### BAJ04

**(SD01TH) Shopify Designer - LATAM 100% REMOTO**

Source: <https://recruitcrm.io/apply/17884566834530148048PgI?source=Jobspage&new=yes>. Fetched UTC: `2026-11-01T20:49:58.141427+00:00`. Location: LATAM per role title; remote with Americas / LATAM time-zone overlap. Review status: `operator_reviewed_v1`.

**Operator answer: eligible.** Role-specific live title explicitly states LATAM 100% REMOTO, which includes Costa Rica, and the description has compatible remote Americas/LATAM schedule overlap without a narrower hiring-country restriction. Eligibility relies on the title's geographic permission, not time-zone overlap alone.

Exact evidence from the parent-page title and description:

```text
(SD01TH) Shopify Designer - LATAM 100% REMOTO
```

```text
Remote, with working hours overlapping Americas / LATAM time zones
```

```text
Full-time, ongoing engagement
```

**Remaining verification limits:** the user accepted title-level permission; country-specific engagement terms remain unverified. The description's timezone phrase would not independently establish legally confirmed LATAM hiring.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full parent-page visible text:

```text
(SD01TH) Shopify Designer - LATAM 100% REMOTO

Share This Job

Apply To This Job
Close
```

Full posting text — description iframe (`about:blank`):

```text
We're hiring a Shopify front-end designer with strong conversion rate optimization (CRO) and A/B testing experience on behalf of a direct-to-consumer men's skincare & grooming brand. You'll design, build, and continuously improve landing pages, homepages, and product pages with one clear goal: turn existing website traffic into measurably higher conversion and revenue.

What you'll do

Design and build front-end Shopify landing pages, homepages, and product pages to marketing requirements
Plan, run, and analyze A/B tests to lift conversion rates
Own a CRO testing roadmap, bringing a proven playbook of what works for D2C brands
Continuously test and iterate to improve conversion performance
Take on complementary design and commerce work (graphic design, packaging, merchandising) to round out the role

What we're looking for

Proven +5 years of experience designing and building landing pages within Shopify
Hands-on A/B testing and CRO experience
Background working with D2C brands, ideally in men's skincare, grooming, or an adjacent consumer space
A demonstrated CRO playbook or portfolio with measurable results
Front-end design and testing skills
Strong English communication skills 

Nice to have

Graphic and packaging design
Broader e-commerce / merchandising experience
Familiarity with AI tools for performance marketing, SEO/AEO, and content

Details

Full-time, ongoing engagement
Remote, with working hours overlapping Americas / LATAM time zones
Start date: as soon as we find the right person
```

## Akurey

Source artifact: `review-akurey-kake.json`. Board: <https://www.akurey.com/careers/>. All four selected detail pages returned HTTP 200, were linked from OPEN POSITIONS, showed APPLY NOW, and had no closure notice; source vacancy status is `apparently_active`. Publication/closing dates: not supplied. The observed board had five named active roles, all LATAM, and one excluded Python/Data Engineering talent database. Geographic outcome diversity was therefore unavailable within Akurey.

Live links use `requirements?pId=...`, unlike the older path-shaped catalog sample. Discovery impact is unverified; no catalog edit or matcher test was performed.

### AKU01

**AI Systems Engineer / Agentic Engineer**

Source: <https://www.akurey.com/careers/requirements?pId=307>. Fetched UTC: `2026-11-01T20:51:48.326084Z`. Location: LATAM. Vacancy evidence: live board link, APPLY NOW, specific temporary six-month position. Review status: `operator_reviewed_v1`.

**Operator answer: eligible.** The role's own Job Location is broad LATAM, which includes Costa Rica. No narrower foreign-only location is stated. The employer footer is corroborating context, not the basis for assigning the vacancy to a particular city.

Exact evidence from the posting:

```text
Job Location
LATAM
```

```text
Contract Duration: This is a temporary position with a 6-month contract.
```

```text
SAN JOSE, COSTA RICA
```

**Why uncertainty remains:** employer has not confirmed hiring availability or the contract arrangement for this particular applicant. General careers benefits may not apply to temporary/freelance terms. The footer is the employer's address context, not a candidate-location requirement.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full posting text:

```text
# AI SYSTEMS ENGINEER / AGENTIC ENGINEER

APPLY NOW

Modality
Full-time / Freelance

Job Location
LATAM

## WHAT YOU WILL BE DOING

Contract Duration: This is a temporary position with a 6-month contract.

- Build and ship within the existing agent architecture and AI SDLC.
- Use AI to create and maintain agents and skills that accelerate delivery through the backlog.
- Run agent swarms as part of daily engineering work.
- Ship AI-powered and agentic features into production across the landing page and CRM platforms.
- Deliver output well above the current pace of roughly one small pull request per day.
- Proactively flag, fix, or backlog small issues and tech debt found during assigned work.

## WHAT WE'RE LOOKING FOR

- An AI-forward working style, using AI to create and maintain agents and skills that accelerate the backlog.
- Proven experience running agent swarms in real delivery work.
- Demonstrated experience shipping AI-powered or agentic features that are actually in production, not just proofs of concept.
- Solid hands-on experience building within an existing agent architecture.
- Strong, practical skills working in Node.js and TypeScript.
- Familiarity with AWS cloud infrastructure.
- Hands-on experience working with Postgres databases.
- A demonstrated track record of meaningful, consistent shipped output.
- Hands-on experience with the stack: React, Tailwind, GraphQL, AWS, Postgres, DynamoDB, and TypeScript.

### Culture Fit

We are looking for someone who:

- Moves with urgency and doesn't wait for permission.
- Treats AI as a real capability multiplier rather than a buzzword.
- Measures success by how much they unblock and upskill others.
- Builds momentum without leaving people behind.
- Stays calm under uncertainty, since the roadmap and team composition are still being shaped, and thrives in that environment.
- Is decisive on people and process without requiring consensus.

## WHAT WOULD BE A PLUS?

- CRM and marketing automation domain and technical experience.
- Terraform or OpenTofu experience for infrastructure as code.
- A .NET or Windows background, given the CRM side of the platform.
- Exposure to Ruby, Python, Java, or Clojure codebases.
- Experience with MySQL, GrapesJS, or Radix.

APPLY NOW

LET'S WORK TOGETHER

Be sure about the quality of your future

SEE ALL OPEN POSITIONS

Employer footer location: SAN JOSE, COSTA RICA
```

### AKU02

**Marketo/Adobe Specialist**

Source: <https://www.akurey.com/careers/requirements?pId=309>. Fetched UTC: `2026-11-01T20:51:48.334395Z`. Location: LATAM. Vacancy evidence: live board link, APPLY NOW, specific three-month contract, no closure notice. Review status: `operator_reviewed_v1`.

**Operator answer: eligible.** Explicit LATAM Job Location includes Costa Rica. Eastern business-hours overlap is a schedule requirement, not US residence or US-only hiring.

Exact evidence from the posting:

```text
Job Location
LATAM
```

```text
At least 3–4 hours of overlap with US Eastern business hours.
```

```text
Contract Duration: This is a temporary position with a 3-month contract, with the possibility of extension.
```

**Why uncertainty remains:** specific daily schedule and extension are not confirmed. General careers benefits may not apply to a freelance contract.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full posting text:

```text
# MARKETO/ADOBE SPECIALIST

APPLY NOW

Modality
Freelance

Job Location
LATAM

## WHAT YOU WILL BE DOING

- Build and execute email campaigns, nurture programs, and landing pages end-to-end.
- Build and troubleshoot responsive email templates and landing pages from scratch using HTML/CSS.
- Manage smart lists, segmentation, imports, and suppression/compliance handling (GDPR, duplicates).
- Work with Marketo–Salesforce bi-directional sync, including lead scoring, field mapping, and troubleshooting failed sync rules.
- Own Jira tickets end-to-end, documenting status, comments, and decisions.

## WHAT WE'RE LOOKING FOR

- Contract Duration: This is a temporary position with a 3-month contract, with the possibility of extension.
- 3–5 years of hands-on Marketo experience.
- Marketo Certified Expert (or at minimum, Certified Associate).
- Strong HTML/CSS skills.
- Working knowledge of Marketo–Salesforce bi-directional sync.
- List/audience management experience.
- Comfortable owning Jira tickets end-to-end as the system of record.
- Strong written English and comfort communicating directly with stakeholders. One of our team members will own ticket intake QA at the start, but the expectation is that this person grows into direct stakeholder contact over time.
- At least 3–4 hours of overlap with US Eastern business hours.
- Self-directed and proactive — when there's no ticket in the queue, they should flag their open bandwidth and propose what they'd work on next, with sign-off before starting, rather than waiting idle for an assignment.

## WHAT WOULD BE A PLUS?

- Prior experience working inside a ticket-based intake model for an in-house team, versus project-based agency work
- B2B SaaS/enterprise marketing background

APPLY NOW

LET'S WORK TOGETHER

Be sure about the quality of your future

SEE ALL OPEN POSITIONS

Employer footer location: SAN JOSE, COSTA RICA
```

### AKU03

**Business Data Analyst (AI-Assisted Analytics)**

Source: <https://www.akurey.com/careers/requirements?pId=304>. Fetched UTC: `2026-11-01T20:51:48.333575Z`. Location: LATAM; explicit LATAM residence requirement. Vacancy evidence: current board link, APPLY NOW, named employee role, no closure notice. Review status: `operator_reviewed_v1`.

**Operator answer: eligible.** Both Job Location LATAM and an explicit must-reside-in-LATAM requirement include a Costa Rica resident. This is actual regional residence wording, not merely a timezone reference.

Exact evidence from the posting:

```text
Job Location
LATAM
```

```text
This person must reside in LATAM.
```

**Why uncertainty remains:** country-specific employment terms and benefit applicability require employer confirmation; no narrower country restriction was visible.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full posting text:

```text
# BUSINESS DATA ANALYST (AI-ASSISTED ANALYTICS)

APPLY NOW

Modality
Employee

Job Location
LATAM

## WHAT YOU WILL BE DOING

- Design, develop, and deliver analytics and reporting for business partners, building self-service capabilities through AI-assisted analytics platforms.
- Configure, govern, and optimize semantic layers and AI analytics tools (e.g., PromptQL, Omni) so partners get reliable, trustworthy answers.
- Partner with the business to craft the right solution, balancing immediate and long-term needs against effort and impact.
- Develop strong collaborative relationships with key business stakeholders.
- Guide partner roadmap plans and investments through opportunity sizing and decision support.
- Be part of a collaborative team with high visibility across the organization.

## WHAT WE'RE LOOKING FOR

- This person must reside in LATAM.
- English level C1.
- 3+ years in an analytics role.
- Degree in a business or quantitative field (e.g., business, economics, mathematics, statistics, computer science).
- Strong experience partnering with product managers and/or business stakeholders on data-driven decision-making.
- Communication skills that simplify complex problems for cross-functional partners.
- Strong SQL and Python skills.
- Hands-on experience with AI-assisted or semantic-layer analytics platforms (e.g., PromptQL, Omni, or similar).
- Comfort working alongside AI agents: guiding, validating, and getting value from their outputs.
- Experience with BI and data visualization tools (e.g., Looker) and dashboard best practices.
- Able to build, maintain, and optimize data pipelines using best practices.
- Prioritization skills with business impact in mind, and the ability to be pragmatic where appropriate.
- Intellectual curiosity, a fast learner, and skilled at asking the right questions.
- Passion for technology and eagerness to adopt new tools and technical concepts.
- Experience working autonomously and taking ownership of projects.

## WHAT WOULD BE A PLUS?

- Retail or e-commerce background, especially with merchandising and customer metrics.

APPLY NOW

LET'S WORK TOGETHER

Be sure about the quality of your future

SEE ALL OPEN POSITIONS

Employer footer location: SAN JOSE, COSTA RICA
```

### AKU04

**Senior Database Engineer / DBA**

Source: <https://www.akurey.com/careers/requirements?pId=308>. Fetched UTC: `2026-11-01T20:51:48.307545Z`. Location: LATAM. Vacancy evidence: current board link, APPLY NOW, specific six-month freelance position, no closure notice. Review status: `operator_reviewed_v1`.

**Operator answer: eligible.** LATAM Job Location includes Costa Rica. Pacific-time overlap and 24x7 on-call are working-pattern constraints, not a requirement to live in a Pacific-region country.

Exact evidence from the posting:

```text
Job Location
LATAM
```

```text
English fluency sufficient for real-time incident communication, with meaningful business hours overlap with Pacific time
```

```text
Carry 24x7 on-call for SQL alerts, lead incident response and root cause analysis for query plan regressions, TempDB contention, and deadlocks or blocking
```

**Why uncertainty remains:** Pacific overlap hours and on-call rotation are unspecified. General careers benefits may not apply to the temporary freelance engagement.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full posting text:

```text
# SENIOR DATABASE ENGINEER / DBA

APPLY NOW

Modality
Freelance

Job Location
LATAM

## WHAT YOU WILL BE DOING

### About the Role:

This role owns a roughly 65TB Microsoft SQL Server estate running in a highly available, clustered configuration, supporting the Insightly CRM product directly. This is production database work for a live, customer-facing SaaS platform, not an analytics or reporting role.

Contract Duration: This is a temporary position with a 6-month contract.

### What this role will do:

- Lead maintenance of the production SQL Server environment in an Always On availability group configuration, including failover handling, sync delay troubleshooting, and read replica load management
- Carry 24x7 on-call for SQL alerts, lead incident response and root cause analysis for query plan regressions, TempDB contention, and deadlocks or blocking
- Maintain database security: TDE, auditing, certificate and key rotation, and monitoring for anomalous access patterns
- Run quarterly cumulative update patching across environments, index and statistics maintenance, and partitioning and archiving on multi-terabyte databases without blocking production
- Own capacity planning for storage, CPU, and memory, and manage the recurring cycle of environment refreshes (staging, dev, QA)
- Review developers' T-SQL and schema changes before deployment, and advise on table and index design
- Support customer instance migrations between regions (sold as a service) and database prep for those moves
- Handle confidential or legally sensitive data requests when they arise, including from law enforcement, following established process
- Design and test disaster recovery for the database environment

## WHAT WE'RE LOOKING FOR

- 7 years of hands-on Microsoft SQL Server DBA experience in a 24x7 production environment, not SQL development alone
- 3 years of direct experience with Always On availability groups, including failover handling and sync delay troubleshooting
- Experience managing multi-terabyte production databases, including partitioning, archiving, and index and statistics maintenance without blocking production
- Experience leading incident response and root cause analysis for production database issues
- Experience with database security practices: TDE, auditing, certificate and key rotation
- English fluency sufficient for real-time incident communication, with meaningful business hours overlap with Pacific time

## WHAT WOULD BE A PLUS?

- Experience supporting a SaaS CRM or similarly high-transaction-volume application
- Exposure to Snowflake, Redshift, or AWS Aurora alongside transactional SQL Server work
- Experience with customer instance migrations or similar large-scale data movement projects
- Experience in a SOC 2, HIPAA, or PCI-regulated environment
- Comfort handling legally sensitive or confidential data requests as part of a defined process

APPLY NOW

LET'S WORK TOGETHER

Be sure about the quality of your future

SEE ALL OPEN POSITIONS

Employer footer location: SAN JOSE, COSTA RICA
```

### Akurey — separately captured careers context

Source: <https://www.akurey.com/careers/>. Artifact timestamp: `2026-11-01T20:51:48Z`; this is the detail-batch UTC second, not an independently clocked board retrieval. The board context was captured immediately before that batch. These are general benefits, not detail-page text or a promise for each freelance engagement. Worldwide-sounding WFH marketing does not override role-level LATAM location.

```text
# CAREERS

LET’S CREATE THE FUTURE

Become a part of our team and explore open roles and exciting career opportunities.

## BENEFITS

We value every member of our team. These are some of the ways we take care of our people.

### WFH

We believe happy people, teamwork, and the right tech tools, can successfully deliver any project from any place in the world.

### ENVIRONMENT

We care for the people. We excel in building trust, cooperation, and bright smiles between our collaborators.

### CAREER PATH

We are as strong as the weakest of our links. That's why we invest in all our team members' personal and professional growth.

### FLEXIBLE HOURS

Sometimes life can't wait from 8 to 5. We can arrange flexible work schedules to accommodate your essential matters.

### WELLNESS

Good physical and mental health, life-work balance, and overall happiness in our collaborators are the first steps toward the success of our business.

### PAID TIME OFF

Better known as Banana Days just because it's fun! These extra days off are paid and can be earned through the years of working at Akurey.

## OPEN POSITIONS

Check out our available positions and find where you fit best in our team.

Employer footer location: SAN JOSE, COSTA RICA
```

## Kake

Source artifact: `review-akurey-kake.json`. Board: <https://kake.co/jobs>. All four selected detail pages returned HTTP 200, appeared on the live board, and contained matching vacancy text, unfilled screening questions, and Apply Now without a closure notice. Source vacancy status is `apparently_active`; publication/closing dates are not supplied. Posting-bearing content was top-level; auxiliary reCAPTCHA/about:blank frames were not needed and tokens were not retained.

### KAK01

**Senior AI/LLM Engineer (Python)**

Source: <https://kake.co/jobs/senior-ai-llm-engineer-python-56a4536c4a20>. Fetched UTC: `2026-11-01T20:52:15.575237Z`. Location: Remote; permitted countries unstated. Review status: `operator_reviewed_v1`.

**Operator answer: eligible.** Human override from unknown under opportunity-screening v1: the role's “Work globally, get paid globally” benefits and Fully Remote wording suffice to retain the opportunity. The 55+ country community count alone is not hiring permission. PST overlap is a separate schedule condition; legally confirmed hiring in Costa Rica remains unverified.

Exact evidence from the posting (artifact nonbreaking spaces retained where quoted):

```text
Our community spans 45,000+ engineers across 55+ countries; join a culture where great people stay, grow, and thrive (and love eating kake!).
```

```text
Competitive Pay in USD: Work globally, get paid globally.
```

```text
Fully Remote: Simply put, we trust you.
```

```text
US Timezone Overlap: 5h–6h daily PST
```

**Remaining verification limits:** no explicit permitted-country list or Costa Rica/LATAM hiring statement. The user accepted “Work globally” with Fully Remote for screening, not as legally definitive vacancy scope. No actual geographic contradiction is asserted. Evidence quotations preserve artifact nonbreaking spaces; matching against the full text requires whitespace normalization.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full posting text:

```text
# Apply for the Senior AI/LLM Engineer (Python)

## Senior AI/LLM Engineer (Python) - Remote

Kake is a remote-first company and a people-first global community of senior engineers. Kake engineers are behind some of the world’s most innovative products (Brands you’ve heard!), from Fortune 500 to fast-growing companies. We believe it’s not where your table is, but what you bring to the table that matters. Our community spans 45,000+ engineers across 55+ countries; join a culture where great people stay, grow, and thrive (and love eating kake!).

Senior AI/LLM Engineers with strong Python experience, skilled in designing, building, and productionizing LLM-powered applications and AI systems at scale. It's a great fit for people who enjoy working at the intersection of software engineering and applied AI, and who take ownership from prototyping through production deployment.

## What you’ll build and own

- Design, build, and deploy LLM-powered features and applications using Python.
- Develop and maintain backend services and APIs (e.g., FastAPI) that expose AI/LLM capabilities to other systems.
- Build and optimize RAG pipelines, including embeddings, vector search, and retrieval strategies.
- Design, test, and iterate on prompts, agents, and orchestration flows using frameworks such as LangChain, LlamaIndex, or similar.
- Integrate with LLM providers and APIs (e.g., OpenAI, Anthropic, open-source models) and manage tradeoffs around cost, latency, and quality.
- Evaluate model outputs systematically, building tooling and metrics to test accuracy, safety, and regression across iterations.
- Work with containerized environments and data infrastructure (e.g., PostgreSQL, Redis, vector databases) to support reliable AI systems in production.
- Collaborate with cross-functional stakeholders to translate ambiguous product needs into technically sound AI solutions.

## Core Requirements

- Strong proficiency in Python and experience with FastAPI or similar backend frameworks.
- Experience working with LLM APIs (e.g., OpenAI, Anthropic, or similar) and frameworks such as LangChain or LlamaIndex.
- Experience with RAG architectures, embeddings, and vector databases (e.g., Pinecone, Weaviate, pgvector, or similar).
- Hands-on experience with Docker and containerized development environments.
- Experience working with PostgreSQL, Redis, or similar data stores.
- Strong experience writing functional and integration tests, including evaluation frameworks for AI/LLM output quality.
- Excellent written and verbal communication skills in English.
- Ability to work independently in a remote, fast-paced environment.

## Nice-to-Have

- Experience fine-tuning or evaluating open-source LLMs.
- Familiarity with prompt engineering best practices and agentic workflows.
- Experience with distributed systems, streaming (e.g., Kafka), or large-scale applications.
- Background in machine learning fundamentals (e.g., scikit-learn, PyTorch, or TensorFlow).
- Comfortable working flexible hours to overlap with distributed teams across different time zones.

## Additional

US Timezone Overlap: 5h–6h daily PST

## Why Join Kake?

The icing on the Kake:

- Competitive Pay in USD: Work globally, get paid globally.
- Fully Remote: Simply put, we trust you.
- Better Me Fund: We invest in your personal growth and passions.
- Compassion is Badass: Join a community that invests in social good.

Ready for your piece of the Kake? Apply now!

Quick note: Due to the high volume of applications, only shortlisted candidates will be contacted.

## Visible application area and screening questions

Location

Upload CV

Click or drag file to this area to upload

Supports .pdf,.doc,.docx up to 10MB

Describe a production-grade AI system you’ve built (e.g., involving LLMs, RAG, or NLP). What were the biggest technical challenges, and how did you ensure scalability and reliability? *

How many years of experience do you have with Python, LLMs, RAG pipelines, NLP systems, and cloud platforms (AWS, GCP, or Azure), including AI orchestration? *

Are you comfortable working across backend, frontend, and cloud infrastructure to ship end-to-end features independently? *

Please Select

Apply Now
```

### KAK02

**Senior Product Data Scientist**

Source: <https://kake.co/jobs/senior-product-data-scientist-98b35d2c5ff6>. Fetched UTC: `2026-11-01T20:52:15.868313Z`. Location: remote; Latin America timezone overlap only. Review status: `operator_reviewed_v1`.

**Operator answer: eligible.** Human override from unknown under opportunity-screening v1: “Latin America Timezone Overlap: 7h of daily overlap with GMT-3 required.” together with the remote-work requirement is sufficient to retain this opportunity absent an excluding restriction. The timezone phrase is not a residence statement or legally confirmed LATAM hiring permission; actual overlap feasibility remains separate.

Exact evidence from the posting:

```text
Latin America Timezone Overlap: 7h of daily overlap with GMT-3 required.
```

```text
Ability to work independently in a remote, fast-paced environment.
```

**Why uncertainty remains:** permitted hiring countries are unstated. Meeting the required daily overlap is an individual scheduling question. No benefits section was visible; benefits are not inferred from other Kake roles.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full posting text:

```text
# Apply for the Senior Product Data Scientist

## Summary

We're looking for a Product Data Scientist who is deeply focused on understanding user behavior and driving product decision-making through experimentation. This person will work closely with product managers, engineers, and design to identify opportunities, run structured A/B tests, and uncover insights that directly influence the product roadmap. The ideal candidate is comfortable owning experiments end-to-end — from hypothesis to rollout — and translating complex data into clear direction for the product team.

## Key Responsibilities

- Analyze user behavior patterns, product usage flows, drop-offs, and engagement trends.
- Design, execute, and evaluate A/B tests and other experimentation frameworks to validate product decisions.
- Partner with product teams to define key metrics, success criteria, and hypotheses for upcoming features.
- Build dashboards and self-serve tools that help the product team understand the health of the product.
- Work with data engineers to ensure clean, reliable, and accessible data for analysis.
- Translate complex findings into clear, actionable insights for stakeholders.

## Core Requirements

- Strong experience with A/B testing, experiment design, hypothesis testing, and interpreting experiment results.
- Solid skills in SQL and Python for data manipulation and analysis.
- Experience working with data warehousing and workflow tools (Snowflake, Airflow, Superset, Eppo, etc.).
- Background in product analytics or product-facing data science roles.
- Ability to communicate insights clearly and influence product strategy using data.
- Excellent written and verbal communication skills in English.
- Ability to work independently in a remote, fast-paced environment.

## Additional

Latin America Timezone Overlap: 7h of daily overlap with GMT-3 required.

## Visible application area and screening questions

Location

Upload CV

Click or drag file to this area to upload

Supports .pdf,.doc,.docx up to 10MB

Have you worked with A/B testing or controlled experiments in production? *

Please Select

Which of the following best describes your involvement in experiments? *

Please Select

What best describes your recent work? *

Please Select

Apply Now
```

### KAK03

**Senior Backend (Go) Engineer (Mexico Only)**

Source: <https://kake.co/jobs/senior-backend-go-engineer-mexico-only-8f085dfa24f3>. Fetched UTC: `2026-11-01T20:52:15.794874Z`. Location: Guadalajara, Mexico; hybrid. Review status: `operator_reviewed_v1`.

**Operator answer: ineligible.** Mexico Only in the title, a Guadalajara-based hybrid role and a minimum two-days-per-week on-site screening question establish a foreign-only geographic requirement. The global technology company description is client context, not broad hiring permission.

Exact evidence from the posting:

```text
Apply for the Senior Backend (Go) Engineer (Mexico Only)
```

```text
This is a hybrid role based in Guadalajara, Mexico, requiring regular in-office presence.
```

```text
Do you have availability to work on-site in Guadalajara, Mexico, at least 2 days per week? *
```

**Why uncertainty remains:** no relocation or exception policy was visible; none is assumed. No benefits section was visible in this detail page.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full posting text:

```text
# Apply for the Senior Backend (Go) Engineer (Mexico Only)

This is a hybrid role based in Guadalajara, Mexico, requiring regular in-office presence.

We’re looking for a Senior Backend Engineer with strong experience in Go to build and evolve backend systems for financial and operational workflows. This role focuses on developing scalable services that support processes such as invoicing, payments, and purchase orders, while integrating with enterprise systems. You’ll be working in partnership with a global technology company operating in the media and digital services space, contributing to a modern backend platform that combines traditional systems with AI-driven development practices.

## Key Responsibilities

- Design, build, and maintain backend services using Go
- Develop and support systems related to financial operations (invoices, payments, purchase orders)
- Work with AWS to build scalable, reliable cloud-based solutions
- Contribute to integrations with external systems, including ERP platforms (e.g. SAP)
- Collaborate on evolving an existing system that includes .NET components
- Build and maintain asynchronous and event-driven systems where appropriate
- Leverage AI tools and agentic development approaches to improve development speed and quality
- Take ownership of features from design through delivery and iteration
- Collaborate with cross-functional teams to ensure reliable and scalable solutions

## Core Requirements

- 5+ years of backend development experience, including at least 3+ years with Go
- Strong experience with cloud (AWS preferable)
- Familiarity with AI-assisted development tools (e.g. Claude Code or similar) and agentic workflows
- Experience working with or integrating into existing/legacy systems
- Strong understanding of backend architecture and scalable systems
- Good communication skills and strong sense of ownership
- Ability to work independently in a fast-paced environment

## Nice-to-Have

- Experience building or supporting financial or billing systems, e.g. invoices, payments, POs (highly preferred)
- Experience developing financial or operational applications
- Experience with asynchronous or event-driven architectures
- Experience integrating with ERP systems (e.g. SAP)
- Exposure to mixed tech stacks (e.g. Go + .NET environments)

## Additional

-Location: Guadalajara, Mexico

## Visible application area and screening questions

Location

Upload CV

Click or drag file to this area to upload

Supports .pdf,.doc,.docx up to 10MB

Do you have availability to work on-site in Guadalajara, Mexico, at least 2 days per week? *

Please Select

How many years of experience do you have as a Backend Engineer? *

Please Select

What is your proficiency in Go? *

Please Select

Please select the option that best reflects your ability to communicate in English in a professional setting. *

Please Select

Apply Now
```

### KAK04

**Finance & Accounting Manager (Operations + FP&A)**

Source: <https://kake.co/jobs/finance-accounting-manager-operations-fp-a-eae5a7b6f30b>. Fetched UTC: `2026-11-01T20:52:15.690877Z`. Location: remote but must be based in or very close to Austin, Texas. Review status: `operator_reviewed_v1`.

**Operator answer: ineligible.** An explicit must-be-based-in-or-near-Austin residence requirement excludes working from Costa Rica. Remote-first company culture and a remote role do not override that restriction. The earlier phrase “hybrid role spanning” describes combined finance duties; the decisive geography is the separate Location Requirement.

Exact evidence from the posting:

```text
This is a remote role, but candidates must be based in Austin, TX or very close to Austin to support occasional in-person collaboration and stronger alignment with our HQ.
```

```text
Are you currently located in/near Austin, TX? *
```

```text
Remote-first culture (Austin-based team hub)
```

**Why uncertainty remains:** the radius meant by “very close to Austin” is unspecified but does not reasonably include Costa Rica. No relocation or geographic exception is stated.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full posting text:

```text
# Apply for the Finance & Accounting Manager (Operations + FP&A)

## About Kake

Kake is a remote-first company helping teams build better with a human-curated community supported by AI, powered by positivity. Learn more at kake.co.

We invest in our people and our communities through programs like Compassion Is Badass (supporting boots-on-the-ground social impact causes), A Better Me (an annual stipend to invest in yourself—health, growth, gear, and more), and various other initiatives.

## The Role

Kake is hiring a hands-on finance and accounting operator to help run our financial engine end-to-end while partnering with leadership on forecasting, budgeting, and performance visibility.

This is a hybrid role spanning accounting operations (AP/AR, payroll, close, bookkeeping) and forward-looking finance (cash flow forecasting, budgeting, KPI reporting). You’ll work closely with the Kake leadership team to tighten processes, improve accuracy, and build repeatable systems that scale.

## What You’ll Do

### Accounting operations & close

- Own day-to-day bookkeeping and month-end close process: reconciliations, clean financials, and a consistent monthly reporting cadence.
- Maintain strong internal controls and clean audit trails.
- Maintain clean, reliable financials, including chart of accounts hygiene and consistent categorization.

### Accounts Receivable (AR) — invoicing, collections, and cash timing

- Own AR end-to-end: invoice creation, delivery, payment tracking, and reconciliation.
- Maintain accurate AR aging reports and provide regular visibility into expected receipts and cash flow timing.
- Partner with leadership and client-facing teams to resolve billing questions, disputes, and invoice exceptions quickly and professionally.

### Accounts Payable (AP) — vendor payments, approvals, and controls

- Own AP workflows: vendor onboarding, bill intake, coding, approval routing, and on-time payments.
- Improve documentation and controls (approval thresholds, audit trail hygiene, repeatable SOPs) to reduce risk and prevent errors.
- Forecast near-term outflows and keep leadership informed about upcoming payables and cash needs.

### Payroll, commissions, and variable compensation

- Own payroll operations end-to-end: pay runs, month-to-month variability, reimbursements, and auditing.
- Gather and validate inputs, ensure calculation accuracy, handle exceptions, and produce clear reporting for leadership and stakeholders.

### FP&A: forecasting, budgeting, and leadership reporting

- Build and maintain rolling forecasts and support budgeting cycles.
- Track and explain key drivers and variances month-over-month and year-over-year to support decisions.
- Track and report burn (burn rate, runway, cash-in vs. cash-out) and provide insight into what’s driving changes.
- Deliver KPI reporting and simple dashboards that leadership can use, including trends and narrative insights.

### International payments & multi-currency

- Support international vendor/contractor payments and multi-currency considerations.
- Ensure consistent handling of conversion rates and FX impacts in bookkeeping and reporting.

### Systems & process improvement

- Own and evolve finance workflows using QuickBooks, Excel/Google Sheets, and Airtable (and related tooling).
- Create and maintain SOPs so finance operations are reliable, scalable, and resilient.
- Identify opportunities to reduce manual work, improve accuracy, and strengthen visibility into the business.

## What We’re Looking For

- 5–7+ years of progressive experience in finance/accounting roles (SMB / growth-stage experience strongly preferred).
- Demonstrated ownership of AP/AR, payroll operations, and month-end close in a hands-on capacity.
- Strong forecasting and budgeting skills; comfortable building models and explaining them clearly.
- Strong attention to detail and auditability.
- Experience supporting international payments and multi-currency considerations.
- High proficiency in Excel/Google Sheets, strong working knowledge of QuickBooks, and comfort working in Airtable or similar tools.
- Strong judgment, professionalism, and reliability.
- Clear communicator who can partner effectively with executives

## Nice to Have

- Experience in tech, software services, marketplaces, staffing, or development-adjacent businesses.
- Familiarity with sales compensation design and administration.
- CPA license and experience with tax accounting and guidance.
- Comfort improving workflows through lightweight automation and strong documentation.

## Benefits & Perks

- Health, dental, and life insurance benefits
- Unlimited PTO
- “A Better Me” annual personal / professional growth stipend
- Remote-first culture (Austin-based team hub)

## Location Requirement

This is a remote role, but candidates must be based in Austin, TX or very close to Austin to support occasional in-person collaboration and stronger alignment with our HQ.

## Visible application area and screening questions

Location

Upload CV

Click or drag file to this area to upload

Supports .pdf,.doc,.docx up to 10MB

Do you have direct experience independently owning accounting operations (including all AP/AR, payroll, close, bookkeeping, forecasting, and budgeting)? [Select only one] *

Please Select

If yes, how many years of experience? *

Are you currently located in/near Austin, TX? *

Please Select

What is your expected salary? *

Is there anything else you'd like us to know about your experience, skills, or accomplishments that you feel makes you a great fit for this role? *

Apply Now
```

## Nearshore Business Solutions

Source artifact: `review-nbs-jobsity.json`. Board: <https://www.careers-page.com/nearshore-business-solutions?page=1>. Four selected named roles were among 65 observed openings with readable detail pages and application affordances; source vacancy status is `active_listed`, not independently employer-confirmed. Publication/closing dates were not visible. “US companies” describes clients, not candidate residence.

### NBS01

**Senior Freight Accountant – US Logistics**

Source: <https://www.careers-page.com/nearshore-business-solutions/job/RYWV9V5R>. Fetched UTC: `2026-11-01T20:53:14.422059+00:00`. Acquisition: HTTP 200 readable detail; listed among 65 openings, specific full-time client role, not a pool. Location: Remote – Latin America. Review status: `operator_reviewed_v1`.

**Operator answer: eligible.** Explicit remote Latin America scope includes Costa Rica. Pacific hours and US logistics/accounting experience describe schedule and professional context, not US residency. CPA is preferred, not a stated mandatory license or work-authorization restriction.

Exact evidence from the posting:

```text
Location: Remote – Latin America
```

```text
Schedule: Pacific Time Zone hours
```

```text
Work remotely: Join a growing US logistics business from Latin America.
```

**Why uncertainty remains:** no Costa Rica-specific contracting or work-authorization confirmation is stated. Publication and closing dates were not visible.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full posting text:

```text
Company Website
Senior Freight Accountant – US Logistics
Apply for Position
Or refer someone
Job Openings  Senior Freight Accountant – US Logistics
About the job Senior Freight Accountant – US Logistics
Senior Freight Accountant – US Logistics

Location: Remote – Latin America
Type of Contract: Full-Time
Salary Range: Market Rates
Schedule: Pacific Time Zone hours

Our client is seeking an experienced Senior Freight Accountant with a background in US freight brokerage or transportation accounting to take ownership of their accounting operations. You will manage day-to-day accounting, financial reporting, and month-end close while helping build efficient processes for a growing logistics company. This is an opportunity to make a direct impact, work closely with leadership, and grow alongside the business.

Key Responsibilities
Manage full-cycle accounting, including general ledger, journal entries, accruals, and month-end close.
Oversee accounts payable and receivable, including carrier payments, customer invoicing, collections, and cash application.
Reconcile bank accounts, credit cards, factoring accounts, and intercompany balances.
Prepare monthly financial statements, cash flow forecasts, and profitability reports.
Ensure freight charges, carrier settlements, and customer invoices are accurate and properly documented.
Improve accounting processes, strengthen financial controls, and support automation initiatives.
Coordinate with external accountants on tax filings, 1099s, and audits.
Must-Have Qualifications
8+ years of accounting experience, including general ledger and month-end close ownership.
Direct experience in US freight brokerage, transportation, or logistics accounting.
Strong understanding of carrier settlements, factoring, quick pay, accessorials, and freight billing.
Experience with multi-entity accounting, financial statements, and account reconciliations.
Strong knowledge of US GAAP and accrual accounting.
Proficiency in QuickBooks or similar accounting software, TMS platforms, and advanced Excel.
Bachelor's degree in Accounting or Finance, with exceptional attention to detail and strong problem-solving skills.
Preferred Qualifications
CPA certification.
Experience improving accounting workflows or implementing automation.
Experience working with US-based companies in a remote environment.
Why Consider This Opportunity?
Make an impact: Take ownership of accounting operations and help shape financial processes.
Work remotely: Join a growing US logistics business from Latin America.
Grow professionally: Work closely with leadership and contribute to important business decisions.
Build for the future: Help create scalable accounting systems and controls as the company expands.
Apply for Position
Or refer someone
Share
```

### NBS02

**Product Manager**

Source: <https://www.careers-page.com/nearshore-business-solutions/job/7X7X93X3>. Fetched UTC: `2026-11-01T20:53:15.964448+00:00`. Acquisition: HTTP 200 readable detail, live opening with specific software product responsibilities, not a pool. Location: Remote – Latin America Preferred. Body title: Software Product Manager. Review status: `operator_reviewed_v1`.

**Operator answer: eligible.** LATAM is explicitly preferred and includes Costa Rica; preferred is not exclusive. EST availability is a schedule condition, not a US residency restriction.

Exact evidence from the posting:

```text
Location: Remote – Latin America Preferred
```

```text
Language Requirements: Fluent English; ability to work primarily in Eastern Standard Time (EST)
```

**Why uncertainty remains:** no country-by-country hiring or authorization list is stated. The board title is Product Manager while the body calls it Software Product Manager; both are retained.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full posting text:

```text
Company Website
Product Manager
Apply for Position
Or refer someone
Job Openings  Product Manager
About the job Product Manager
Software Product Manager

Location: Remote – Latin America Preferred
Type of Contract: Full-Time | Remote
Salary Range: Market Rates
Language Requirements: Fluent English; ability to work primarily in Eastern Standard Time (EST)

Our client is seeking a hands-on Software Product Manager with experience in B2B SaaS product ownership, backlog management, and rapid feature delivery to join their growing team. You will own core features of a healthcare compliance SaaS platform, driving execution from discovery and prototyping through development and launch. Your work will directly impact product quality, engineering efficiency, and the adoption of intuitive, self-service software by small and mid-sized businesses.

Key Responsibilities
Own core product features from discovery through delivery, ensuring alignment with product priorities and customer needs.
Manage backlog health, sprint readiness, delivery quality, and roadmap execution in a fast-paced product environment.
Write clear, actionable Jira epics, user stories, and acceptance criteria that enable engineering teams to execute efficiently.
Build prototypes and wireframes using AI tools to define user flows, validate concepts, and improve in-app experiences.
Lead backlog grooming, sprint planning, and requirements refinement using a lightweight, pragmatic delivery approach.
Translate product vision into prioritized roadmap plans and develop business cases grounded in customer needs.
Gather and synthesize customer and prospect feedback, collaborate closely with product and engineering leadership, and drive improvements that increase usability and self-service adoption.

Must-Have Qualifications
At least 2 years of product management experience in a B2B SaaS environment.
Proven experience owning product delivery in a startup or fast-moving organization.
Strong hands-on experience with Jira, including user story writing, acceptance criteria, and backlog management.
Technical fluency and the ability to collaborate effectively with engineers while understanding system constraints.
Experience building products for small and mid-sized business (SMB) customers, with an emphasis on simplicity, usability, and speed.
Strong communication and collaboration skills, with the ability to work effectively with product leaders and engineering teams.
Fluent English, availability to work primarily in EST, and a proactive, self-directed mindset comfortable with ambiguity.

Preferred Qualifications
Experience in healthcare technology, compliance software, or regulated industries.
Experience designing intuitive, self-service product workflows that improve customer adoption and confidence.
Familiarity with AI-powered tools for prototyping, wireframing, and improving product management workflows.
Experience working in lean product teams that prioritize rapid iteration, independent ownership, and continuous delivery.

Ideal Candidate Profile
The ideal candidate is an execution-focused product manager who takes ownership of outcomes rather than simply managing tickets. They are comfortable making decisions with incomplete information, identifying risks proactively, and working directly with engineering teams to move features from concept to production quickly. They thrive in collaborative, fast-paced environments where customer impact, clear requirements, and practical problem-solving take priority over heavy processes.

Apply for Position
Or refer someone
Share
```

### NBS03

**Product Manager, Self Service Enablement**

Source: <https://www.careers-page.com/nearshore-business-solutions/job/Y6733X65>. Fetched UTC: `2026-11-01T20:53:38.693119+00:00`. Acquisition: accessible live detail, specific full-time contractor role. Location: Bogotá, Colombia header versus Remote – Latin America body. Review status: `operator_reviewed_v1`.

**Operator answer: unknown.** Visible header and board name Bogotá, Colombia, while the body explicitly says Remote – Latin America. Preserve both and flag the conflict rather than assuming the header is administrative or the body overrides it.

Exact evidence from the posting:

```text
Bogotá, Colombia
```

```text
Location: Remote – Latin America
```

```text
Type of Contract: Full-Time | Remote | Contractor
```

**Why uncertainty remains:** whether Bogotá is a restrictive hiring location or an ATS/default location is unresolved. Costa Rica-specific contracting and work authorization are not stated.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full posting text:

```text
Company Website
Product Manager, Self Service Enablement
Bogotá, Colombia
Apply for Position
Or refer someone
Job Openings  Product Manager, Self Service Enablement
About the job Product Manager, Self Service Enablement
Job Title: Product Manager, Self-Service Enablement

Location: Remote – Latin America
Type of Contract: Full-Time | Remote | Contractor
Salary Range: Market Rates
Language Requirements: English proficiency required

Our client is seeking a skilled Product Manager with proven B2B SaaS experience and a track record of launching successful self-service product experiences. You will play a key role in transforming their App Builder platform into a self-service product that customers can discover, purchase, build with, and operate independently. Your work will directly impact customer adoption, time to value, product usability, and the platform’s readiness for general availability.

Key Responsibilities
Own the end-to-end self-service enablement strategy, including onboarding journeys, navigation, in-product guidance, and support experiences that help customers build and launch applications independently.
Map user journeys across business builders, tenant administrators, executive sponsors, and enterprise architects to identify adoption barriers and prioritize product improvements.
Translate internal knowledge into reusable templates, pattern guides, validation rules, safe defaults, and actionable in-product guidance.
Partner with engineering and design to deliver intuitive navigation, onboarding flows, error prevention, usage dashboards, cost estimates, and application health indicators.
Define and monitor product success metrics, including activation, time to first application, user drop-off, error rates, support contacts, and self-service conversion; use analytics and experiments to drive improvements.
Collaborate with Operations, Customer Success, Finance, Legal, and program leadership to develop predictable usage limits, support processes, escalation paths, and self-service purchasing and plan management experiences.
Lead self-service readiness assessments throughout phased beta rollouts, incorporating customer feedback and reporting progress toward general availability, including the goal of enabling 1,000 builders and reducing time to first application to under one hour for templated use cases.
Must-Have Qualifications
3+ years of product management experience in B2B SaaS, including ownership of at least one self-service or product-led experience successfully launched with measurable adoption results.
Demonstrated success improving product metrics such as activation rates, time to value, self-service conversion, or support contacts per user.
Strong UX sensibility and experience collaborating with designers on onboarding flows, navigation, empty states, error handling, dashboards, and usability testing.
Working knowledge of platform concepts such as multi-tenancy, quotas, rate limits, permissions, and usage metering to make informed product trade-offs.
Strong data fluency, including defining event instrumentation, interpreting funnel and cohort analyses, and using product analytics to guide decisions.
Excellent written communication skills, with the ability to explain product limitations, errors, and technical issues clearly while maintaining customer trust.
Ability to manage phased product rollouts with real customers, limited resources, evolving requirements, and competing priorities.
Preferred Qualifications
Experience with low-code or no-code application builders, workflow automation platforms, or developer tools.
Experience launching AI-powered features, including considerations around model costs, output quality, and user trust.
Familiarity with HRM/HCM, ITSM/ESM, ERP, manufacturing, or supply chain systems and their data sensitivity requirements.
Experience with usage-based or tiered pricing, self-service checkout, and subscription management.
Experience managing beta or early-access programs, including participant communications, feedback collection, and iterative product improvements.
Familiarity with enterprise requirements such as single sign-on (SSO), audit logging, GDPR, and SOC 2.
Apply for Position
Or refer someone
Share
```

### NBS04

**Field Service Engineer – Semiconductor Equipment**

Source: <https://www.careers-page.com/nearshore-business-solutions/job/V6767X36>. Fetched UTC: `2026-11-01T20:53:18.996701+00:00`. Acquisition: HTTP 200 detail, live opening with specific compensation, duties, and mandatory residency requirements. Location: Querétaro, Mexico – Field-Based. Review status: `operator_reviewed_v1`.

**Operator answer: ineligible.** Explicit current residency in Querétaro and regular on-site support exclude work from Costa Rica despite the Remote/Field-Based label. Acceptance of foreign degrees does not remove residency requirements.

Exact evidence from the posting:

```text
Candidates must currently reside in Querétaro, Mexico.
```

```text
This is a predominantly field-based position requiring regular on-site customer support.
```

```text
Employment will be managed through an Employer of Record (EOR) arrangement via Global Payroll.
```

**Why uncertainty remains:** specific Mexico work-authorization criteria are not stated; EOR employment is not proof of sponsorship. No relocation exception is offered in the visible text.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full posting text:

```text
Company Website
Field Service Engineer – Semiconductor Equipment
Querétaro, Qro., Mexico
Apply for Position
Or refer someone
Job Openings  Field Service Engineer – Semiconductor Equipment
About the job Field Service Engineer – Semiconductor Equipment

Job Title: Field Service Engineer – Semiconductor Equipment
Location: Querétaro, Mexico – Field-Based
Type of Contract: Full-Time | Remote/Field-Based (EOR)
Salary Range: $4,000–$5,000 USD/month, depending on experience.

Our client is seeking a skilled Field Service Engineer with hands-on experience installing, calibrating, maintaining, and troubleshooting semiconductor equipment, particularly die sorting and chip attach machines. You will work primarily at customer sites in Querétaro, supporting equipment installation, process engineering, technical training, and ongoing maintenance. Your expertise will help ensure equipment reliability, optimal machine performance, and customer satisfaction.

Key Responsibilities
Install and commission semiconductor equipment, including die sorting and chip attach machines, by interpreting technical drawings, electrical schematics, and engineering documentation.
Assemble, position, align, level, and connect machine modules, components, wiring, and cables according to engineering specifications.
Calibrate and configure machine components, including servo controller driver boards, Bachmann PLC modules, bondhead and flipper assemblies, and sensors.
Configure PLC software and machine graphical user interfaces (GUI), and perform equipment testing to validate functionality and performance.
Troubleshoot mechanical, electrical, electronic, software, and process-related issues, identify root causes, and implement corrective actions.
Train customer engineering and technical personnel on equipment installation, operation, process development, and preventive maintenance procedures.
Provide on-site and hotline technical support, assist with production process engineering as needed, maintain service documentation, and ensure compliance with established procedures and quality standards.

Must-Have Qualifications
Bachelor's degree in Electronics Engineering, Electrical Engineering, Mechanical Engineering, or a related field; an associate degree in a relevant discipline is also acceptable. Equivalent foreign degrees are accepted.
At least two years of relevant experience installing, maintaining, and troubleshooting semiconductor equipment, machinery, systems, and components.
At least two years of experience supporting customer process engineering and working in a cleanroom environment.
Hands-on experience calibrating semiconductor equipment components, including controller driver boards, PLC modules, and precision assembly modules.
Experience configuring PLC software and machine graphical user interfaces (GUI) using computer-assisted engineering software.
Experience with vision software used in semiconductor equipment.
At least two years of experience training technical or engineering personnel on equipment installation, operation, and maintenance, and resolving customer equipment performance issues.

Preferred Qualifications
Direct experience with die sorting, chip attach, bondhead, or flipper assembly equipment.
Familiarity with Bachmann PLC systems, servo controllers, sensor calibration, and precision machine alignment.
Strong customer-facing communication skills and the ability to work independently at customer facilities.
Flexibility to travel locally to customer sites and provide field-based technical support as required.

Additional Requirements
Candidates must currently reside in Querétaro, Mexico.
This is a predominantly field-based position requiring regular on-site customer support.
Employment will be managed through an Employer of Record (EOR) arrangement via Global Payroll.
Compensation will be determined based on relevant experience and alignment with the client's budget.
Apply for Position
Or refer someone
Share
```

## Jobsity

Source artifact: `review-nbs-jobsity.json`. Board: <https://careers.jobsity.com/>. Source vacancy status for all four is `active_listed`; direct detail text and board labels are observations, not verified publication dates. Publication/closing dates: not supplied. Generic Americas/remote company context is retained separately below, not inherited as role-specific permission. No live US-board vacancy was available; the foreign-only comparator is the Mexico-based PLC role.

### JOB01

**Sales Development Representative, LATAM**

Source: <https://careers.jobsity.com/opportunities/sales-development-representative-latam>. Fetched UTC: `2026-11-01T20:53:31.968874+00:00`. Acquisition: direct detail HTTP 200 with matching title/body; live sidebar label Internal / New; no Always Hiring label. Location: LATAM & US. Review status: `operator_reviewed_v1`.

**Operator answer: eligible.** Role-specific LOCATION: LATAM & US explicitly includes Latin America and therefore Costa Rica; US is an additional region, not the exclusive location.

Exact evidence from the posting:

```text
LOCATION: LATAM & US
```

```text
Sales Development Representative, LATAM
```

**Why uncertainty remains:** no country-by-country contracting or work-authorization criteria are stated. New is a live board label, not a verified publication date.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full posting text:

```text
Sales Development Representative, LATAM

LOCATION: LATAM & US Attention all the sales people!! Jobsity is actively seeking an enthusiastic Sales Development Representative to drive the implementation of our growth strategy within our IT vertical. We're on the lookout for a proactive, entrepreneurial individual who has a genuine passion for sales and is committed to nurturing enduring client relationships.
Key Requirements:
2+ years of experience in a Sales Development, Business Development, or similar outbound prospecting role, preferably in a B2B technology or services environment.
Demonstrable track record of consistently achieving or exceeding activity and pipeline generation quotas.
Strong understanding of the software development life cycle (SDLC) and general knowledge of technology roles (e.g., Full Stack Engineer, DevOps, QA, Product Manager).
Exceptional written and verbal communication skills, with an ability to engage confidently with senior-level executives. C-1 or above English language ability.
Proficiency with sales technology tools, including a CRM (e.g., Salesforce, HubSpot), sales engagement platforms (e.g., Salesloft, Outreach), and LinkedIn Sales Navigator.
High energy, self-starter, and relentless desire to win new business.
High level of personal accountability, and results-driven.
```

### JOB02

**Senior PLC Engineer**

Source: <https://careers.jobsity.com/opportunities/senior-plc-engineer>. Fetched UTC: `2026-11-01T20:53:28.410871+00:00`. Acquisition: direct HTTP 200 matching detail; sidebar IT / Hot; no Always Hiring label. Location: based in Mexico with extended US travel. Review status: `operator_reviewed_v1`.

**Operator answer: ineligible.** The role explicitly seeks someone based in Mexico, excluding a Costa Rica base. Extended US on-site travel and a valid US visa are additional requirements; generic Americas/remote company language does not override them.

Exact evidence from the posting:

```text
We’re looking for a PLC Engineer based in Mexico
```

```text
Availability for extended travel to the US, with on-site periods of around six weeks or longer, and a valid US visa.
```

**Why uncertainty remains:** visa category and Mexico employment authorization are not specified. No sponsorship or relocation promise appears in the visible detail.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full posting text:

```text
Senior PLC Engineer

We’re looking for a PLC Engineer based in Mexico to develop, modify, and troubleshoot PLC programs on Allen-Bradley and Siemens control systems. You’ll travel to customer sites in the US for extended periods (typically six weeks or longer) to support installation, commissioning, and start-up, working directly with customer teams to get systems running smoothly
Key Requirements:
5+ years of hands-on PLC programming in industrial automation environments.
Strong experience with Allen-Bradley and Siemens PLCs.
Availability for extended travel to the US, with on-site periods of around six weeks or longer, and a valid US visa.
```

### JOB03

**Mid-level Android Engineer**

Source: <https://careers.jobsity.com/opportunities/mid-level-android-engineer>. Fetched UTC: `2026-11-01T20:53:30.117909+00:00`. Acquisition: direct HTTP 200 matching detail; sidebar IT / Hot; no Always Hiring label. Location: unstated; full Italian-hours overlap. Review status: `operator_reviewed_v1`.

**Operator answer: unknown.** No candidate country or region is stated in the complete visible role detail. Italian time-zone overlap is a schedule condition, not Italian residency. Generic Americas company context is retained separately but is insufficient to prove this role accepts Costa Rica.

Exact evidence from the posting:

```text
work with full overlap in the Italian time zone (CET/CEST).
```

**Why uncertainty remains:** candidate residence, permitted hiring countries, and work authorization are unstated. Full Italian-hours overlap could be operationally difficult from Costa Rica but is not a geographical exclusion.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full posting text:

```text
Mid-level Android Engineer

We are looking for a mid-level Android Engineer to join a cross-functional feature team within the Android chapter, building and maintaining features in a Kotlin app with Jetpack Compose and an internal design system. You will work on features, migrate legacy XML screens to Compose, and implement asynchronous flows such as background uploads during multi-step forms. You should also use AI coding tools daily and be able to validate their output, communicate clearly in English, and work with full overlap in the Italian time zone (CET/CEST).
Key Requirements:
3+ years of native Android development with Kotlin (main focus in recent years), with good knowledge of Coroutines and Flow.
2+ years of hands-on experience with Jetpack Compose.
Experience with Hilt and clean code/architectural patterns, with at least a conceptual understanding of MVI.
```

### JOB04

**Configuration Specialist**

Source: <https://careers.jobsity.com/opportunities/configuration-specialist>. Fetched UTC: `2026-11-01T20:52:34.218514+00:00`. Acquisition: live sidebar Contact Center / Regular and homepage link; matching URL and selected detail; no Always Hiring label. Location: unstated; U.S.-based clients. Review status: `operator_reviewed_v1`.

**Operator answer: unknown.** The detail has no candidate-location or authorization statement. U.S.-based clients describes customers, not worker residency. Generic company-wide Americas and remote context is preserved separately without turning it into role-specific permission.

Exact evidence from the posting:

```text
a software platform used by U.S.-based clients.
```

```text
Professional English communication skills.
```

**Why uncertainty remains:** Costa Rica availability, hiring countries, employment arrangement, and work authorization are unstated. Regular is a listing label; headcount and closing date are not independently confirmed.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full posting text:

```text
Configuration Specialist

We are looking for a detail-oriented and technically minded Configuration Specialist to support the setup, maintenance, testing, and troubleshooting of a software platform used by U.S.-based clients. In this role, you will translate business and client requirements into accurate system configurations, working with modules, forms, permissions, workflows, and business rules. You will also investigate system issues, determine whether they are related to configuration, data, user behavior, or product functionality, and collaborate with technical teams when escalation is required.
What You’ll Do:
Configure and maintain system modules, forms, permissions, workflows, and business rules.
Translate client and business requirements into effective system configurations.
Troubleshoot configuration and software-related issues and identify root causes.
Test and validate configurations to ensure they meet requirements and quality standards.
Document configuration changes, troubleshooting steps, and resolutions.
Collaborate with Product, Engineering, Operations, Support, and other internal teams.
Communicate technical concepts and solutions clearly to both technical and non-technical stakeholders.
Manage multiple priorities while maintaining a high level of accuracy and attention to detail.
What We’re Looking For:
Experience working with software, SaaS platforms, application support, system configuration, implementation, or technical support.
Strong troubleshooting, analytical, and problem-solving skills.
Ability to understand workflows, system logic, and business requirements.
Excellent attention to detail and commitment to quality.
Strong documentation and communication skills.
Ability to work independently and collaborate effectively across teams.
Professional English communication skills.
Experience with tools or concepts such as SQL, APIs, JSON, CRM platforms, Jira, or workflow management systems is a plus.
```

### Nearshore and Jobsity — separately captured board context

Nearshore source: <https://www.careers-page.com/nearshore-business-solutions?page=1>. Observed date UTC: `2026-11-01`; no separate retrieval second supplied. Generic speculative application invitation is not counted as a vacancy.

```text
Work Remotely with US Companies
We're looking for great people to work with companies abroad
Our Openings
65 Open Positions
Submit your application
We are always looking for bright minds and enthusiastic people from diverse backgrounds, who want to be part of an outstanding team. Even if you don’t find a suitable position below, please do not hesitate to drop us your resume.
```

Jobsity source: <https://careers.jobsity.com/>. Observed date UTC: `2026-11-01`; no separate retrieval second supplied. Company context does not override Mexico-specific roles or resolve missing role geography for the user-accepted JOB03/JOB04 cases.

```text
Apply. Grow. Thrive.
You've built a specialized skill set and honed your problem-solving abilities. Now bring your talent to leading US companies on high-impact projects — whether you're based in Latin America or the United States. Join a distributed team of engineers, making a difference at companies like Intuit, McGraw Hill, Zebra Technologies, and more.
Explore LATAM Opportunities
Explore U.S. Opportunities
We are always looking to add the best talent to our roster of top developers in the Americas. Find your perfect role here and join our team!
Work from Anywhere in the Americas
Jobsity developers are based all over Latin America and the US. No matter where you are, you can grow your career with us while working remotely with top U.S. companies. Join our team from wherever you call home.
100% Remote Positions
Jobsity is a fully-remote company, meaning you can work from wherever you are. All you need is your laptop and wifi.
Work With US-Based Clients
Collaborate with our US clients to complete challenging projects that will take your career to the next level.
```

Jobsity US-board source: <https://careers.jobsity.com/opportunities-us>. Observed date UTC: `2026-11-01`; no separate retrieval second supplied. No US-board vacancy was selected.

```text
Explore U.S.-Based Opportunities
Join a company with a proven track record of placing candidates
Jobsity has spent 14 plus years building remote careers that last, long before remote work was a trend.
Connecting software engineers with leading U.S. companies since 2012
Trusted by Intuit, McGraw Hill, Zebra Technologies, and dozens of other U.S. companies
A team of 500+ professionals across 20+ countries
An industry-leading ~97% average retention rate.
1,300+ candidates placed with U.S. companies since 2018.
Recognized by the Financial Times as one of the Americas’ Fastest Growing Companies and certified as a Minority Business Enterprise
All
No openings found matching your search.
```

Collection anomalies: installed Chrome was used because bundled Chromium was unavailable; no browser download occurred. Jobsity client-side selection could update visible text before URL; Android, PLC, and SDR were reloaded directly to verify agreement. An unselected SRE detail returned HTTP 403 while displaying readable content on a later check; it is not one of these 32. The initial automatic iframe-network caveat is recorded in the provenance section above.

## Railway

Source artifact: `review-railway-oceans.json`. Board: <https://railway.com/careers#open-positions>. Observation window UTC: `2026-11-01T20:51:00Z/2026-11-01T20:53:00Z`; board HTTP 200. All 17 observed board roles said Remote (anywhere), so no restricted-location comparator was available. Four distinct role families were selected. All four details returned HTTP 200; source vacancy status is `board_linked_detail_available` (current board link, substantive description and application affordance, no observed closure notice). Publication/closing dates: not supplied. Funded headcount and application backend are unverified.

Exact board evidence, separately sourced from <https://railway.com/careers#open-positions>:

```text
Work from anywhere
```

```text
We're autonomous, async heavy, and hire globally. Pick a place, a base, or any space. We're happy wherever you are.
```

This is corroborating board context; each selected role also supplies its own “Remote (anywhere)” permission. Initial WebFetch reconnaissance lacked posting links; rendered board capture established them.

### RAI01

**Senior Infra Engineer: Platform**

Source: <https://railway.com/careers/infra-platform>. Fetched UTC: `2026-11-01T20:52:03.021056Z`. Location: Remote (anywhere). Acquisition: HTTP 200 rendered visible text; confirmed board link. Vacancy evidence: role title and “Apply for / this position” control. Review status: `operator_reviewed_v1`.

**Operator answer: eligible.** The role explicitly says Remote (anywhere), corroborated by the board's global-hiring statement. This is affirmative worldwide geography evidence that includes Costa Rica, not an inference from remote alone.

Exact role evidence:

```text
Remote (anywhere)
```

```text
We're globally distributed—and getting more so. Stuff is always happening somewhere.
```

The exact “hire globally” corroboration is the separate board quote above, not text attributed to the detail page.

**Why uncertainty remains:** country-specific employment, payroll, work authorization, and benefits implementation were not verified. On-call is 1–2 weeks per quarter; no fixed timezone restriction is stated. Public board membership is not confirmation that hiring is actively progressing.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full posting text:

```text
Senior Infra Engineer: Platform

Remote (anywhere)

Full-time

Job description

Our core mission at Railway is to make software engineers higher leverage. We believe that people should be given powerful tools so that they can spend less time setting up to do, and more time doing.

Building the infrastructure which powers the Railway engine is the most core problem at Railway. As an infrastructure engineer, you will be directly responsible for exposing the knobs, bells, and whistles that allow the users to build to the wildest dreams. The tools you build will be instrumental in not only scaling internal operations, but scaling the company to infinite and beyond!

“But the world would be a better place if more engineers, like me, hated technology. The stuff I design, if I'm successful, nobody will ever notice. Things will just work, and will be self-managing”

Radia Perlman

Curious? Here are 3 blog posts that dive into exciting projects this team has worked on: 1, 2, 3

Want to learn about our work culture? Here is a multi-part blog series that will help you see the unique ways our team works (Parts 1, 2, 3, and 4).

Required Qualifications/Experience

Expect to need all of these, day to day, to excel at Railway

Built application level code (Golang, GRPC, Postgres, etc)

Made use of OS primitives (syscalls, cgroups, etc)

Delivered projects 0→N. Lead it, scaled it, seen your “brilliant solutions” become tech debt, etc

Operated with agency and urgency (bonus points if remotely!)

Had enough foresight to ship for today, design for 10x, and engineer for 100x in 18mo

Communicated clearly using RFCs/Docs/etc to break up + deliver large, nebulous tasks

Been oncall and know that you will be 1-2 weeks per quarter

Constructed monitoring, alerts, and dashboards to notify you before things break

Built the confidence to say no to 99% of things, and the grit to deliver the 1% that matter

Desired Qualifications/Experience

Expect to go deep in 1-2 of these to do the best work of your life

Baremetal Networking at 1T+ request/mo scale (eBGP/eBPF, L2-L7, Wireguard)

Orchestration at 100m+ containers scale (Runtimes/Hypervisors, Live-migrations, RTOS)

Metrics/Logs/Traces at 1T request/mo scale (Vector, VictoriaMetrics, Clickhouse)

Block storage, filesystems, replication at Petabyte/drive scale (ZFS, Ceph, VirtioFS)

Efficient OCI build/transfer at TBps/sec scale (BuildKit, SOCI, Zot, overlayfs)

Building “datacenters” (in quotes because closet server racks and HomeLabs count!)

We value and love to work with diverse persons from all backgrounds

Things to Know

For better or worse, we're a startup; our team dynamics are different from companies of different sizes and stages.

We're globally distributed—and getting more so. Stuff is always happening somewhere.
We don't expect you to be online all the time, but you'll need to be diligent about your boundaries — your end of day will overlap with someone else's start.
We're a small, high-ownership team that cares deeply about doing exceptional work. We're scaling quickly, which means we rely on leverage—systems over coordination, judgment over process. Expect ambiguity and a fast-moving environment.
You'll own real outcomes. That means making decisions, not just executing—and owning the success, or failure, that comes with them.

Benefits and perks

At Railway, we provide best in class benefits. Great salary, full health benefits including dependents, strong equity grants, equipment stipend, and much more. For more details, check back on the main careers page.

Beyond compensation, there are a few things that we believe that make working at Railway truly unique:

Autonomy: We have very few meetings. Just a Monday and a Friday to go over the Company Board. We think your time is sacred, whether it's at work, or outside of work.
Ownership: We're a company with a high ownership, high autonomy culture. We hope that you'll come in, help us, and over the course of many years do the best work of your life. When we bring you onboard, we expect you to change the company.
Novel problems/solutions: We're a startup that's well funded, with cool problems, which lets us implement novel solutions! We abhor “busywork” and think, whether it's community, engineering, operations, etc there's always opportunity for creative and high leverage solutions.
Growth: We want you to grow with us, but we know that talent is loaned, so when you figure out what area you want to grow in next, whether it's at Railway or outside, we'll make sure you land there.

How We Hire

No tricks. No surprises. Here's the entire process.

1

Talk with us about the role

This is completely open ended and we're just trying to see who you are, what you want to do, and where you wanna go.

2

Work on a small project to discuss in the interview

Asynchronously implement the following:

Architect a Container Provisioning Engine to power something like Railway

Interview Structure (60 Minutes):

Pre-work (before your interview): Complete your solution (advised)

0-5m: introduction

5-50m: Building (or expanding) your solution

50-60m: Questions on Railway/Tech/etc

You can, and SHOULD! ask us questions ahead of time. Ask away!

3

Review your solution with the Team

You'll sit down with someone on the team and go over the above. We'll poke into your solution, as well as get you acquainted with two more members of the team.

Looking for: Learn about your problem solving skills. How you break down a problem and how you present a solution.

4

Meet the Team

You'll meet the Team, which will be comprised of 4 people from vastly different sections of the company.

Looking for: How you work with the rest of the team and communicate.

5

Chat with CEO

Sit down with our founder and CEO for 30 minutes. This is a 1:1, open ended conversation.

6

Offer call

Finally, we will present the offers, hammer out the details about your position, tee up onboarding, and start our journey together.

Final Note: The interview goes both ways. Once again, please ask us things. Many things! Hard things. That's what we're here for.
```

### RAI02

**Senior Product Designer**

Source: <https://railway.com/careers/Product-designer>. Fetched UTC: `2026-11-01T20:52:03.019341Z`. Location: Remote (anywhere). Acquisition: HTTP 200 rendered visible text; confirmed board link. Vacancy evidence: title and “Apply for / this position” control. Review status: `operator_reviewed_v1`.

**Operator answer: eligible.** Explicit Remote (anywhere) role location and a corroborating global-hiring board statement support Costa Rica geography eligibility.

Exact role evidence:

```text
Remote (anywhere)
```

```text
We're globally distributed—and getting more so. Stuff is always happening somewhere.
```

The separately sourced “hire globally” board quote appears at the start of this company section.

**Why uncertainty remains:** Costa Rica payroll, contracting arrangements, work authorization, and country-specific benefits were not verified. No fixed timezone restriction is stated; attention to cross-timezone boundaries is expected. Public board membership is not confirmation that hiring is actively progressing.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full posting text:

```text
Senior Product Designer

Remote (anywhere)

Full-time

Job description

At Railway, we make it extremely simple for engineers to deploy logic without thinking about servers. Our goal is to make developers orders of magnitude more efficient by becoming the operating system for modern development.

By building tooling for infrastructure automation, we seek to make people 100x more effective, and build 100x more impactful systems. Building leverage is a core part of what we do.

We’re seeking a versatile and seasoned Senior Product Designer who strives to create great products, loves collaborating, and takes the initiative. The ideal candidate will derive satisfaction from ideating, designing, and delivering exceptional solutions to hundreds of thousands of developers. This position is tailored for a well-rounded professional who appreciates the art of creative thinking but places equal importance on witnessing their ideas come to life.

“Products are made in a factory but brands are created in the mind.” - Walter Landor

Want to learn about our work culture? Here is a three-part blog series that will help you see the unique ways our team works (Parts 1, 2, 3, and 4).

About the role

Play a pivotal role in Railway's developer experience, redefining the future of software infrastructure.
Collaborate closely with product, support, and engineering to scope, define, design, and ship high-quality features.
Contribute to the overall design direction of the product, leveraging your creativity and problem-solving skills to address complex design challenges and deliver elegant solutions.
Balance user needs, business goals, and technical constraints of design solutions, ensuring that Railway remains user-friendly, scalable, and reliable.
Define workflows, prototypes, and visual design of your projects.
Learn about our users, how they use our platform, understand their pain points, and propose projects to fix them.
Help us maintain and evolve our internal Design System
Work in an ambiguous environment to create levers for design excellence, using process sparingly but effectively.
Contribute to the design culture within the company.

About you

Builder at heart. You and an engineer against the world. Scares some people, excites the right ones.
You have a strong portfolio showcasing your skills in user interface design, interaction design, and visual design. Your portfolio demonstrates your ability to solve complex design problems and showcases your attention to detail.
You care about creating good products, understand what makes a good experience, and constantly think of improvement opportunities for the products you use.
You care about business needs, implications, and the practicality of your solutions and work to deliver clear, intuitive, and appealing human interfaces.
You are a creative thinker who can push the boundaries of design while ensuring a balance between user needs, business goals, and technical constraints.
You feel comfortable designing, illustrating, iterating, and prototyping in Figma, but always look for new tools to open new possibilities (Blender, Spline, Rive, shaders, etc.).
You have experience balancing trade-offs while implementing a feature to ship it on time without compromising the long-term product vision.
You have experience effectively collaborating with cross-functional teams to deliver high-quality designs within project timelines.
You have excellent communication skills and can articulate design decisions and rationale effectively to stakeholders and team members.

We value and love to work with diverse persons from all backgrounds

Things to know

For better or worse, we're a startup; our team dynamics are different from companies of different sizes and stages.

We're globally distributed—and getting more so. Stuff is always happening somewhere.
We don't expect you to be online all the time, but you'll need to be diligent about your boundaries — your end of day will overlap with someone else's start.
We're a small, high-ownership team that cares deeply about doing exceptional work. We're scaling quickly, which means we rely on leverage—systems over coordination, judgment over process. Expect ambiguity and a fast-moving environment.
You'll own real outcomes. That means making decisions, not just executing—and owning the success, or failure, that comes with them.

Benefits and perks

At Railway, we provide best in class benefits. Great salary, full health benefits including dependents, strong equity grants, equipment stipend, and much more. For more details, check back on the main careers page.

Beyond compensation, there are a few things that we believe that make working at Railway truly unique:

Autonomy: We have very few meetings. Just a Monday and a Friday to go over the Company Board. We think your time is sacred, whether it's at work, or outside of work.
Ownership: We're a company with a high ownership, high autonomy culture. We hope that you'll come in, help us, and over the course of many years do the best work of your life. When we bring you onboard, we expect you to change the company.
Novel problems/solutions: We're a startup that's well funded, with cool problems, which lets us implement novel solutions! We abhor “busywork” and think, whether it's community, engineering, operations, etc there's always opportunity for creative and high leverage solutions.
Growth: We want you to grow with us, but we know that talent is loaned, so when you figure out what area you want to grow in next, whether it's at Railway or outside, we'll make sure you land there.

How we hire

No tricks. No surprises. Here's the entire process.

1

Talk with us about the role

This is completely open ended and we're just trying to see who you are, what you want to do, and where you wanna go.

2

Work on a small project to discuss in the interview

Asynchronously implement the following:

Design Exercise
Review the product and identify one improvement (a redesign of an existing area or a new feature that extends functionality). Then, implement one workflow to illustrate your idea—using either Figma screens, a clickable prototype, or code.

You can, and SHOULD! ask us questions ahead of time. Ask away!

3

Review your solution with the Team

You'll sit down with someone on the team and go over the above. We'll poke into your solution, as well as get you acquainted with two more members of the team.

Looking for: Learn about your problem solving skills. How you break down a problem and how you present a solution.

4

Meet the Team

You'll meet the Team, which will be comprised of 4 people from vastly different sections of the company.

Looking for: How you work with the rest of the team and communicate.

5

Chat with CEO

Sit down with our founder and CEO for 30 minutes. This is a 1:1, open ended conversation.

6

Offer call

Finally, we will present the offers, hammer out the details about your position, tee up onboarding, and start our journey together.

Final Note: The interview goes both ways. Once again, please ask us things. Many things! Hard things. That's what we're here for.
```

### RAI03

**Account Executive, Scale**

Source: <https://railway.com/careers/account-executive-scale>. Fetched UTC: `2026-11-01T20:52:09.047915Z`. Location: Remote (anywhere). Acquisition: HTTP 200 rendered visible text; confirmed board link. Vacancy evidence: “We’re hiring our first dedicated Account Executive, Scale to own this segment end to end.” and application control. Review status: `operator_reviewed_v1`.

**Operator answer: eligible.** The specific sales role says Remote (anywhere), corroborated by the board's global hiring policy. Customer segment does not impose a geographic exclusion in the captured role text.

Exact role evidence:

```text
Remote (anywhere)
```

```text
Comfort working remotely in a highly autonomous, high-ownership environment.
```

The separately sourced “hire globally” board quote appears at the start of this company section.

**Why uncertainty remains:** no explicit territory, customer-timezone coverage, or Costa Rica payroll arrangement is specified. Legal hiring eligibility and individual qualifications were not assessed. Public board membership does not confirm that hiring is progressing.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full posting text:

```text
Account Executive, Scale

Remote (anywhere)

Full-time

Job description

At Railway, we take the complexity out of shipping software. We make it simple for engineers to deploy code without thinking about servers, so teams can move faster and spend more time building.

More than 4M developers already build on Railway, and our upmarket motion is compounding across both logos and revenue. As we grow Enterprise, thousands of smaller high-potential accounts need a more focused, scalable motion. That’s where you come in.

We’re hiring our first dedicated Account Executive, Scale to own this segment end to end. You’ll work a high-volume book of accounts: initiating conversations, qualifying opportunities, driving expansion, and managing relationships as they grow into Enterprise. Just as importantly, you’ll build the motion while you run it — qualification, demos, expansion, retention, playbooks, and the tooling underneath.

This is a high-ownership, high-leverage role: you, the product, and the systems you build for yourself. You should be sales-technical at heart — comfortable talking to developers, getting hands-on in the product, and winning on substance rather than a script.

Want to learn about our work culture? Here is a multi-part blog series that will help you see the unique ways our team works (Parts 1, 2, 3, and 4).

About the role

Own the Scale account motion end to end, from first touch through expansion and retention.

Source, qualify, demo, and close your own pipeline across a high-volume book of SMB and mid-market accounts.

Build lightweight systems, tooling, and automations that let one person cover hundreds of accounts effectively.

Develop repeatable playbooks for qualification, demos, expansion, and retention as the motion matures.

Run credible technical conversations with developers and get hands-on in Railway to understand and solve customer problems.

Identify accounts with Enterprise potential and build relationships that can grow with them.

Partner with the broader team by surfacing customer feedback, product gaps, and market signals from the Scale segment.

About you

You’re a great fit if you have:

Full-cycle selling experience in SMB or mid-market, with a track record of sourcing, qualifying, demoing, and closing your own pipeline.

Experience owning volume: hundreds of accounts worked with focused, high-leverage touches rather than a small book of high-touch logos.

Sales-technical fluency. You can hold a real conversation with a developer, understand the problem, and speak credibly about the product.

A builder’s instinct for 0-to-1 work. You define the motion, not just run one that already exists.

A data-driven approach to managing a large account base: you instrument the motion and let the numbers steer it.

Strong written communication. Most of your selling here, internally and externally, happens in writing to developers who hate fluff.

High EQ, low ego, and a bias toward earning trust by being direct, useful, and honest.

Comfort working remotely in a highly autonomous, high-ownership environment.

An interest in computing infrastructure and the developer-tools market. Bonus if you already use Railway or a comparable developer platform.

We don’t require you to check every box on the list, and we generally prefer working with people who are hungry for knowledge in areas they feel they’re lacking in. Please don’t hesitate to shoot us an application if you find Railway and the role interesting!

We value and love to work with diverse persons from all backgrounds.

Things to Know

For better or worse, we're a startup; our team dynamics are different from companies of different sizes and stages.

We're globally distributed—and getting more so. Stuff is always happening somewhere.
We don't expect you to be online all the time, but you'll need to be diligent about your boundaries — your end of day will overlap with someone else's start.
We're a small, high-ownership team that cares deeply about doing exceptional work. We're scaling quickly, which means we rely on leverage—systems over coordination, judgment over process. Expect ambiguity and a fast-moving environment.
You'll own real outcomes. That means making decisions, not just executing—and owning the success, or failure, that comes with them.

Benefits and perks

At Railway, we provide best in class benefits. Great salary, full health benefits including dependents, strong equity grants, equipment stipend, and much more. For more details, check back on the main careers page.

Beyond compensation, there are a few things that we believe that make working at Railway truly unique:

Autonomy: We have very few meetings. Just a Monday and a Friday to go over the Company Board. We think your time is sacred, whether it's at work, or outside of work.
Ownership: We're a company with a high ownership, high autonomy culture. We hope that you'll come in, help us, and over the course of many years do the best work of your life. When we bring you onboard, we expect you to change the company.
Novel problems/solutions: We're a startup that's well funded, with cool problems, which lets us implement novel solutions! We abhor “busywork” and think, whether it's community, engineering, operations, etc there's always opportunity for creative and high leverage solutions.
Growth: We want you to grow with us, but we know that talent is loaned, so when you figure out what area you want to grow in next, whether it's at Railway or outside, we'll make sure you land there.

How We Hire

No tricks. No surprises. Here's the entire process.

1

Talk with us about the role

This is completely open ended and we're just trying to see who you are, what you want to do, and where you wanna go.

2

Work on a small project to discuss in the interview

Asynchronously implement the following:

Technical Interview prompt: Write a GM memo: grow Railway's Business segment 10x

Interview Structure (60 Minutes):

Pre-work (before your interview): Write your memo (advised)

0-5m: Introduction

5-35m: Walking through your solution

35-50m: Alternative approaches you considered

50-60m: Questions on Railway/GTM/etc

You can, and SHOULD! ask us questions ahead of time. Ask away!

3

Review your solution with the Team

You'll sit down with someone on the team and go over the above. We'll poke into your solution, as well as get you acquainted with two more members of the team.

Looking for: Learn about your problem solving skills. How you break down a problem and how you present a solution.

4

Meet the Team

You'll meet the Team, which will be comprised of 4 people from vastly different sections of the company.

Looking for: How you work with the rest of the team and communicate.

5

Chat with CEO

Sit down with our founder and CEO for 30 minutes. This is a 1:1, open ended conversation.

6

Offer call

Finally, we will present the offers, hammer out the details about your position, tee up onboarding, and start our journey together.

Final Note: The interview goes both ways. Once again, please ask us things. Many things! Hard things. That's what we're here for.
```

### RAI04

**Senior Product Marketer**

Source: <https://railway.com/careers/product-marketer>. Fetched UTC: `2026-11-01T20:52:09.131306Z`. Location: Remote (anywhere). Acquisition: HTTP 200 rendered visible text; confirmed board link. Vacancy evidence: role title and application control. Review status: `operator_reviewed_v1`.

**Operator answer: eligible.** The role explicitly permits Remote (anywhere), and the careers board explicitly says it hires globally; Costa Rica is geographically included.

Exact role evidence:

```text
Remote (anywhere)
```

```text
Experience working remotely in a highly autonomous environment
```

The separately sourced “hire globally” board quote appears at the start of this company section.

**Why uncertainty remains:** country-specific legal hiring, payroll, and benefits arrangements were not verified. No fixed timezone restriction is stated. Public board membership does not confirm that hiring is progressing.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full posting text:

```text
Senior Product Marketer

Remote (anywhere)

Full-time

Job description

At Railway, we make it extremely simple for engineers to deploy code without thinking about servers. Our goal is to make developers orders of magnitude more efficient by removing the need to manage infrastructure when developing software.

By building tooling for infrastructure automation, we seek to make people 100x more effective, and build 100x more impactful systems. Building leverage is a core part of what we do.

And we need someone to tell that story.

As a Product Marketer, you will have carte blanche to make sure we are top of mind for every developer who wants to move the world via code.

With nearly 4M developers on platform already, we have the opportunity to build a new kind of cloud provider that changes the industry for the better.

Want to learn about our work culture? Here is a multi-part blog series that will help you see the unique ways our team works (Parts 1, 2, 3, and 4).

“Products are made in a factory but brands are created in the mind.” - Walter Landor

Required Qualifications/Experience

Expect to need all of these, day to day, to excel at Railway

Knowledge of positioning and branding from consumer to enterprise

A deep understanding of the developer tools market

An interest in computing infrastructure in particular

Strong technical writing skills

Clear and succinct communication skills

Strong skills in pitching, refining, and hammering home the product vision

Experience working remotely in a highly autonomous environment

At Railway we believe that great marketing is not an afterthought; it’s great storytelling to make the product and vision crystal clear

Desired Qualifications/Experience

Product Marketers at Railway are expected to wield deep experience across the entire marketing stack, from classical growth hacking techniques, to performance/growth funnel analysis and tuning, to traditional comms, to building engineering-as-marketing campaigns.

Deep experience in top of funnel and inbound growth generation

Strong product experience building funnels, segmenting users, and A/B testing campaigns

Stellar writing skills to personify the voice of the user, sell them a future, and help engineers iterate to launch (and beyond)

Mastery of enterprise buyer decision making and positioning to show up the way 6-7 figure buyers expect

We value and love to work with diverse persons from all backgrounds

Things to know

For better or worse, we're a startup; our team dynamics are different from companies of different sizes and stages.

We're globally distributed—and getting more so. Stuff is always happening somewhere.
We don't expect you to be online all the time, but you'll need to be diligent about your boundaries — your end of day will overlap with someone else's start.
We're a small, high-ownership team that cares deeply about doing exceptional work. We're scaling quickly, which means we rely on leverage—systems over coordination, judgment over process. Expect ambiguity and a fast-moving environment.
You'll own real outcomes. That means making decisions, not just executing—and owning the success, or failure, that comes with them.

Benefits and perks

At Railway, we provide best in class benefits. Great salary, full health benefits including dependents, strong equity grants, equipment stipend, and much more. For more details, check back on the main careers page.

Beyond compensation, there are a few things that we believe that make working at Railway truly unique:

Autonomy: We have very few meetings. Just a Monday and a Friday to go over the Company Board. We think your time is sacred, whether it's at work, or outside of work.
Ownership: We're a company with a high ownership, high autonomy culture. We hope that you'll come in, help us, and over the course of many years do the best work of your life. When we bring you onboard, we expect you to change the company.
Novel problems/solutions: We're a startup that's well funded, with cool problems, which lets us implement novel solutions! We abhor “busywork” and think, whether it's community, engineering, operations, etc there's always opportunity for creative and high leverage solutions.
Growth: We want you to grow with us, but we know that talent is loaned, so when you figure out what area you want to grow in next, whether it's at Railway or outside, we'll make sure you land there.

How we hire

No tricks. No surprises. Here's the entire process.

1

Talk with us about the role

This is completely open ended and we're just trying to see who you are, what you want to do, and where you wanna go.

2

Work on a small project to discuss in the interview

Asynchronously implement the following:

Build, and present, a campaign to move Railway forward

Could be generally, or within a specific vertical (enterprise, top of funnel, etc)

In lieu of the above, you may present a prior campaign you have lead

You can, and SHOULD! ask us questions ahead of time. Ask away!

3

Review your solution with the Team

You'll sit down with someone on the team and go over the above. We'll poke into your solution, as well as get you acquainted with two more members of the team.

Looking for: Learn about your problem solving skills. How you break down a problem and how you present a solution.

4

Meet the Team

You'll meet the Team, which will be comprised of 4 people from vastly different sections of the company.

Looking for: How you work with the rest of the team and communicate.

5

Chat with CEO

Sit down with our founder and CEO for 30 minutes. This is a 1:1, open ended conversation.

6

Offer call

Finally, we will present the offers, hammer out the details about your position, tee up onboarding, and start our journey together.

Final Note: The interview goes both ways. Once again, please ask us things. Many things! Hard things. That's what we're here for.
```

## Oceans Code Experts

Source artifact: `review-railway-oceans.json`. Board: <https://www.oceanscode.com/careers>. Observation window UTC: `2026-11-01T20:51:00Z/2026-11-01T20:54:00Z`; board HTTP 200. The collector inspected all 33 board-linked detail headers: three All LatAm, one US, 29 CO; none explicitly named Costa Rica or CR. Four named roles were selected. CO remains unexpanded, not silently interpreted as a country or US state.

All four selected details returned HTTP 200 with substantive rendered text, confirmed board links, and APPLY FOR THIS POSITION controls, without observed closure notices. Source vacancy status is `board_linked_detail_available`, not independent employer confirmation. Publication/closing dates are not supplied. No separate benefits section was present in these four captures; none is imported from company marketing.

### OCE01

**YR-15 Data Engineer**

Source: <https://www.oceanscode.com/careers/yr-15-data-engineer>. Fetched UTC: `2026-11-01T20:52:15.286500Z`. Location: 100% Remote / All LatAm. Acquisition: HTTP 200 rendered visible text; board-linked role and application control. Review status: `operator_reviewed_v1`.

**Operator answer: eligible.** Role-specific All LatAm geography plus 100% Remote supports working from Costa Rica as part of Latin America. This is regional admission evidence, not remote-only inference.

Exact evidence from the posting:

```text
100% Remote
All LatAm
```

```text
This is a full-time consultancy, with up to 40 weekly hours during regular business times. We operate under a flexible core hours policy to accommodate various schedules, allowing consultants to perform during their peak productivity times. Additionally, we offer the flexibility to work remotely.
```

**Why uncertainty remains:** Costa Rica is regionally covered but not individually named. Regular business times have no explicit timezone. Consulting legal, payment, and country-specific engagement arrangements are unverified. No separate benefits section appears. Board membership does not establish recruiter activity.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full posting text:

```text
YR-15 Data Engineer
100% Remote
All LatAm
Senior
Full Time

JOB DESCRIPTION

Technologies: SAP HANA, SAP (BW + ERP), ETL

Oceans Code Experts is looking for a Data Engineer to join our team for a consulting position. This role involves working with ETL processes, SAP Data & Analytics Tools, and data governance in a detail-oriented manner. The ideal candidate should have advanced English proficiency and at least 5 years of experience in similar roles.

JOB REQUIREMENTS

Job Responsibilities
Manage ETL processes using Alteryx, AWS services, or other reporting tools
Utilize SAP Data & Analytics Tools such as SAP BW, HANA, and ERP for data management
Stay up-to-date with the latest technologies in the Data & Analytics field
Focus on managing multiple reports and processes efficiently
Ensure data governance and operational excellence
Job Requirements
5+ years of experience in data governance, data management, or data operational roles
Advanced English proficiency, written and spoken
Expertise in ETL processes, SAP BW, ERP, and SAP HANA
Familiarity with Python, AWS, Azure, Advanced Excel, database queries, SQL, and Microsoft Power
Strong knowledge of software engineering best practices
Experience with Git, API integration, and testing
Nice to Have
Experience with advanced Excel functions
Knowledge of AWS and Azure cloud services

Position Type and Expected Hours of Work

This is a full-time consultancy, with up to 40 weekly hours during regular business times. We operate under a flexible core hours policy to accommodate various schedules, allowing consultants to perform during their peak productivity times. Additionally, we offer the flexibility to work remotely.
```

### OCE02

**PH-25 Senior Jira Administrator**

Source: <https://www.oceanscode.com/careers/ph-25-senior-jira-administrator>. Fetched UTC: `2026-11-01T20:52:15.284017Z`. Location: 100% Remote / All LatAm. Acquisition: HTTP 200 rendered visible text; board-linked role and application control. Review status: `operator_reviewed_v1`.

**Operator answer: eligible.** Role-specific All LatAm and 100% Remote affirm regional eligibility inclusive of Costa Rica; the description also explicitly says Fully remote.

Exact evidence from the posting:

```text
100% Remote
All LatAm
```

```text
Fully remote.
```

```text
Up to 40 hours per week with flexible core hours.
```

**Why uncertainty remains:** Costa Rica is not individually named; engagement arrangements are unverified. The summary requires at least five years while requirements say +3; that nongeographic inconsistency is preserved. The six-month contract has no start/end date, so remaining availability is unknown. No separate benefits section appears. Board membership does not establish recruiter activity.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full posting text:

```text
PH-25 Senior Jira Administrator
100% Remote
All LatAm
Senior
Full Time

JOB DESCRIPTION

Technologies: Jira, Workflows, ScriptRunner

Job Summary

Oceans Code Experts is looking for a talented Senior Jira Administrator to join our team. In this role, you will be responsible for managing, configuring, and optimizing the Atlassian Jira environment for our client, ProArch. You will collaborate closely with Project Managers and cross-functional teams to ensure Jira is aligned with organizational workflows and reporting requirements. This position requires at least 5 years of experience with advanced proficiency in Jira and agile methodologies.

JOB REQUIREMENTS

Job Responsibilities
Configure and maintain the Atlassian Jira Software and/or Jira Service Management.
Design and implement workflows in Jira to support agile methodologies like Scrum, Kanban, and SAFe.
Create and manage user permissions, groups, and project roles within Jira.
Utilize JQL to build filters, reports, and dashboards to meet project needs.
Implement Jira Automation for streamlining processes and improving efficiency.
Troubleshoot and resolve issues within the Jira environment with a keen attention to detail.
Communicate effectively with technical and non-technical stakeholders to translate requirements into Jira configurations.
Collaborate with teams to ensure Jira is effectively supporting project delivery and agile operations.
Job Requirements
+3 years of hands-on experience as a Jira Administrator in a professional setting.
Proficiency in configuring Jira Software and/or Jira Service Management.
Strong understanding of agile methodologies such as Scrum, Kanban, and SAFe.
Experience with Jira Automation and workflow design.
Solid knowledge of JQL for building filters, reports, and dashboards.
Excellent problem-solving skills and meticulous attention to detail.
Strong communication skills to convey technical concepts to non-technical stakeholders.
Advanced English proficiency, both written and spoken.
Nice to Have
Atlassian Certified Professional (ACP) certification.
Experience with Confluence administration and Space management.
Familiarity with Script Runner (Groovy scripting) or other advanced Jira plugins.
Knowledge of REST API usage for Jira integrations.
Position Type and Expected Hours of Work
Full-time consulting position.
Up to 40 hours per week with flexible core hours.
Fully remote.
Contract duration: 6 months.
```

### OCE03

**OC-29 Sales Representative**

Source: <https://www.oceanscode.com/careers/oc-29-sales-representative>. Fetched UTC: `2026-11-01T20:52:21.447862Z`. Location header: 100% Remote / US. Acquisition: HTTP 200 rendered visible text; board-linked role and application control. Review status: `operator_reviewed_v1`.

**Operator answer: ineligible.** The user accepted the existing case-specific exclusion based on the role's US header rather than All LatAm. This retained judgment does not establish a general rule that any named foreign header overrides remote wording. The employer does not expressly say US residents only; sales-territory ambiguity remains unverified.

Exact evidence from the posting:

```text
100% Remote
US
Senior
Full Time
```

```text
This role involves selling software development services, custom solutions, and IT consulting to U.S.-based clients.
```

```text
Fully remote
```

**Why uncertainty remains:** US is displayed as role location, but the body discusses US clients and does not explicitly require US residence. If the header describes sales territory rather than worker location, revisit the accepted case-specific label in a future version; employer clarification is needed. No US work-authorization or Costa Rica contracting statement was captured. No separate benefits section appears. Board membership does not establish recruiter activity. This is the principal named-header policy edge case, not proof of an explicit “US only” sentence.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full posting text:

```text
OC-29 Sales Representative
100% Remote
US
Senior
Full Time

JOB DESCRIPTION

Job Summary

Oceans Code Experts is looking for a Sales Representative with advanced English proficiency to join our team. This role involves selling software development services, custom solutions, and IT consulting to U.S.-based clients.

JOB REQUIREMENTS

Job Responsibilities
Cultivate and manage relationships with technology executives and decision-makers in the U.S.
Close six-figure software projects and managed services engagements.
Create software development estimates and project proposals.
Utilize Agile methodologies to guide software development processes.
Collaborate with software architects, developers, and technical teams.
Scope software projects based on business requirements.
Conduct consultative selling to address client needs effectively.
Negotiate contracts and commercial agreements with clients.
Deliver engaging presentations to showcase our services effectively.
Utilize CRM platforms such as HubSpot, Salesforce, or Zoho for sales activities.
Job Requirements
5+ years of experience selling software development services, custom solutions, IT consulting, or technology services.
Proven track record of closing six-figure software projects and/or managed services engagements.
Experience selling to U.S.-based clients.
Strong understanding of Agile software development methodologies.
Ability to scope software projects from business requirements.
Excellent consultative selling skills.
Experience collaborating with software architects, developers, and technical teams.
Strong presentation and communication skills.
Experience negotiating contracts and commercial agreements.
Advanced English proficiency, written and spoken.
Nice to Have
Previous experience working for a software development company, digital agency, or IT consulting firm.
Experience selling Nearshore or Offshore software development services.
Knowledge of cloud technologies (AWS, Azure, GCP).
Understanding of AI, machine learning, automation, and modern software architectures.
Experience with SaaS platforms and enterprise applications.
Existing network of technology executives and decision-makers within the U.S.
Position Type and Expected Hours of Work
Full-time consulting position
Up to 40 hours per week
Flexible core hours
Fully remote
```

### OCE04

**Ax-01 Program Management Lead**

Source: <https://www.oceanscode.com/careers/ax-01-program-management-lead>. Fetched UTC: `2026-11-01T20:52:21.455955Z`. Location header: Hybrid / CO; body: Fully remote. Acquisition: HTTP 200 rendered visible text; board-linked role and application control. Review status: `operator_reviewed_v1`.

**Operator answer: unknown.** The header says Hybrid and CO, while the work-conditions section says Fully remote. CO is not expanded in the visible posting. Neither generic remote language nor an unexpanded, conflicting location establishes permission to remain in Costa Rica; employer clarification is required.

Exact evidence from the posting:

```text
Hybrid
CO
Senior
Full Time
```

```text
Fully remote.
```

```text
Manage vendor-led and globally distributed delivery models.
```

**Why uncertainty remains:** Hybrid conflicts with Fully remote; attendance cannot be resolved. CO may be a geographic code, but no expansion or office city is given and it is not evidence of Costa Rica eligibility. Globally distributed delivery models describes program work, not worldwide worker residence. No Costa Rica or All LatAm permission or separate benefits section appears. Board membership does not establish recruiter activity.

Operator confirmation: accepted by user conversation; recorded 2026-11-02 UTC (runtime date). Reviewer: user; no employer confirmation.

Full posting text:

```text
Ax-01 Program Management Lead
Hybrid
CO
Senior
Full Time

JOB DESCRIPTION

Job Summary

Oceans Code Experts is looking for a talented Ax-01 Program Management Lead to join our team. This role involves overseeing IT project and program management, leading Project Managers within a formal PMO structure, and delivering programs and portfolios in regulated, enterprise IT environments.

JOB REQUIREMENTS

Job Responsibilities
Oversee IT project and program management activities.
Lead Project Managers within a formal PMO structure.
Deliver programs and portfolios in regulated, enterprise IT environments.
Utilize advanced risk, dependency, and decision management capabilities.
Manage vendor-led and globally distributed delivery models.
Operate at a program or portfolio level.
Collaborate with senior stakeholders and delivery teams.
Ensure adherence to PMO service models, governance, and financial controls.
Job Requirements
Bachelor’s degree in Technology, Business, or related field (Master’s preferred or equivalent experience).
+12 years of progressively senior experience in IT project/program management.
Strong program and portfolio delivery leadership experience in enterprise IT environments.
Experience leading Project Managers within a formal PMO structure.
Proven ability to lead and influence senior stakeholders and delivery teams.
Advanced risk, dependency, and decision management capabilities.
Experience with PMO and ITSM tooling (ServiceNow PPM, Jira, Azure DevOps, or equivalent).
English proficiency (written and spoken).
Nice to Have
Familiarity with ServiceNow PPM, Jira, Azure DevOps, or equivalent tools.
Experience in managing vendor-led and globally distributed delivery models.
Position Type and Expected Hours of Work
Full-time consulting position.
Up to 40 hours per week.
Flexible core hours.
Fully remote.
```
