# Issue: a remote list that includes Latin America is never called eligible (BL04)

Status: open, not started. Recorded 2026-11-21.

## Summary

BL04 is the only blind posting the eligibility model rejected wrongly: across 5
runs it answered ineligible 2 times and unknown 3 times, never eligible. Its 2
ineligible answers are the blind set's only wrongful ineligibles. This is a real
decision mistake, not a quote-check problem: every answer passed the quote check.

The likely cause is that the policy gives the model two conflicting rules for
this pattern.

## The posting

- **Blind case:** `tests/fixtures/postings/opportunity-screening-blind-v1/BL04/`
- **Company and role:** doola, Customer Care Agent.
- **URL:** https://jobs.lever.co/doola/999a0f10-0738-42f7-a9f6-5c7192e74873
- **The only location wording in the posting:**
  ```
  Customer Care Agent
  Remote in Chile / Remote in Philippines / Remote in Latin America / Remote in Argentina
  Customer Success /
  Full-time /
  Remote
  ```
- **Label:** eligible. One of the listed options is "Remote in Latin America", and
  Latin America includes Costa Rica.

## What the model answered (`gpt-5.4-mini-2026-03-17`, medium effort)

| Runs | Decision | Reason given |
|---|---|---|
| 3 | unknown | "The posting gives a LATAM remote permission but also limits the role to specific countries, creating a location conflict." |
| 2 | ineligible | "The role is limited to Remote in Chile, the Philippines, Latin America, or Argentina, which excludes a candidate based in Costa Rica." |

The ineligible answers name Latin America as one of the allowed places and still
say Costa Rica is excluded, which is a reasoning error. The unknown answers treat
Chile and Argentina as concrete locations that conflict with the Latin America
permission.

## Why: the policy pulls in two directions

The prompt (`SYSTEM_PROMPT` in `src/job_eligibility/prompt.py`) has three rules
that touch this pattern:

- **Rule 1 (ineligible):** "…a remote arrangement stated as located in named
  places that exclude Costa Rica, such as `Remote in Peru or Chile`, makes the role
  ineligible." BL04 is written the same way ("Remote in Chile / Remote in …"),
  which invites the ineligible reading.
- **Rule 2 (unknown):** "A concrete place named as the role's location is a
  conflict with a regional permission even when that place lies inside the
  permitted region…" Chile and Argentina lie inside Latin America, which invites
  the unknown reading.
- **Rule 3 (eligible):** "A list that adds other places to such a permission stays
  eligible." This is the rule the label relies on.

Rules 2 and 3 contradict each other here. The policy never says how **one list of
alternative locations** ("any of these") differs from **two separate statements
that disagree**. An example of the second kind is NBS03: a "Bogotá, Colombia"
header plus a "Remote – Latin America" body line, correctly unknown.

## Proposed rule (needs the operator's decision)

A single list of alternative locations is a union of options:

- **eligible** if any option includes Costa Rica (Costa Rica, LATAM, the Americas,
  worldwide, and so on);
- **ineligible** if every option excludes Costa Rica;
- **unknown** only when separate statements disagree, as today.

Checked against the existing labels, nothing else changes:

| Case | Wording | Label | Under the union rule |
|---|---|---|---|
| GOL03 | "Remote in Argentina or Mexico" | ineligible | ineligible (every option excludes Costa Rica) |
| NBS03 | separate city header and LATAM line | unknown | unknown |
| GOL04 | header LATAM and a perks line "South America" | unknown | unknown |
| BL08 | header "Argentina" and "Location: LATAM (Remote)" | unknown | unknown |
| BL15, BL44, BL50 | lists that name Costa Rica | eligible | eligible |
| BL04 | list including "Latin America" | eligible | eligible |

## Proposed fix

1. **Confirm the union rule.** This is a policy decision.
2. **Change the prompt:**
   - word rule 1's example as places that *all* exclude Costa Rica;
   - state the union rule in rule 3;
   - limit rule 2's "concrete place" conflict to separate statements;
   - add one synthetic example, such as "Remote in Uruguay / Remote in Latin
     America / Remote in Spain" → eligible, citing "Remote in Latin America". The
     existing test rejects any example text found in a corpus posting.
3. **Re-run the evaluations:** development, held-out and blind, 5 runs each, about
   $1.50 in total. The prompt fingerprint changes, so the saved answers can't be
   reused.
4. **Check for regressions:** NBS03, GOL04 and BL08 must stay unknown, and GOL03
   must stay ineligible.
5. **Be careful reading the result.** BL04 motivated the change, so the blind set
   no longer tests this pattern independently. Collect a few new blind postings
   with mixed country and region lists to confirm it.

## Scope

This was 1 of 51 blind postings, but the pattern is common on LATAM-focused boards.
doola, Vintti, CommandLink, Newsela and Auxis all publish country lists. Those
that name Costa Rica already pass. The risk is in lists that include a region
(LATAM, the Americas) without naming Costa Rica.

The blind set's other decision flips are separate, smaller issues: BL11 (a bare
"North Carolina Region" label), BL29 (once unknown instead of eligible), and on
the held-out set GOL04 (a perks line overriding the header).
