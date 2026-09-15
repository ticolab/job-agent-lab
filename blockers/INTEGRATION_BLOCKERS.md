# Integration Blockers — Round 2

## Purpose

This file records boards the pipeline **cannot currently extract**, and why the
failure is structural rather than incidental. It exists so accumulated evidence
can drive an informed decision about which architectural extensions are worth
building.

Add an entry when an integration attempt terminates because the pipeline cannot
reach the data, or ships via a workaround that hides a real gap. Prompt-tuning
misses, live-run flake, model quirks, and rate limits do **not** belong here.

## Scope

This file tracks boards that cannot ship today. Most are also listed in
`blocked-companies.json`; an entry may instead sit in `new-companies.json` when
its blocker has an **agreed** unblocking plan and is queued for that work. Each
entry states its queue file explicitly, so the two never have to be inferred
from each other. When a board ships, its entry is deleted: the mechanism that
unblocked it lives in the code and in `TABNINE.md`, and git history keeps the
investigation. Boards merely awaiting integration with no blocker are not
entries here.

## Pattern classes

Classes C1–C10 came out of round 1; C11 onward were added during round 2. A
class is *closed* when the architecture handles its shape generically, which is
not the same as every board of that shape being integrated.

| Class | Shape | Status |
|---|---|---|
| C1 | Job cards are non-anchor elements; the destination exists only in framework state, an XHR response, or a `<form>` submit | **Open** on the DOM path. Closable per board when a backing API is reachable. Two boards: Luflox (router state), Instacredit (form submit) |
| C2 | The only filter affordance is a free-text search box — no location facet | **Open** outside Greenhouse. Closed for Greenhouse tenants, which bypass the DOM entirely |
| C3 | Location filter is a native `<select>` or combobox the agent must discover and drive | Closed by the prompt playbook, including attribute-anonymous selects behind custom widgets |
| C4 | Listings hidden behind collapsed accordions, each independently openable | Closed — `hooks.expand_selector` runs bounded click-all rounds (exemplar: Jobsity). The *exclusive* accordion variant is **not** this class; see C20 |
| C5 | The real board is embedded cross-origin | Closed for Greenhouse tenants via the API path |
| C6 | Anchors inside open shadow roots | Closed — the matcher pierces open shadow roots |
| C7 | The listing is split across pages | Closed — the pagination walker |
| C8 | Anchors are present but CSS-hidden | Closed — the anchor visibility gate |
| C9 | Chrome links share the job prefix at a shallower depth | Closed — `LinkRule.min_depth` |
| C10 | Anchors inside same-origin frames | Closed — the matcher walks same-origin frame documents |
| C11 | A URL pre-filter is destroyed when the agent re-applies the same filter through a stateful widget | Closed — `pre_filter_urls` removes the agent from the loop. The prompt-side `filter_already_applied` hook is *not* a reliable closure (see note below) |
| C12 | Runtime works, but no honest snapshot exists — filter state is not URL-addressable *and* the unfiltered board dwarfs the walker cap | Closed — an API adapter's recorded payload replaces the page fixture |
| C13 | Pagination markup defeats every next-control signal (icon-only control, per-`<li>` page indicators) | Closed — class-token and load-more signals, plus a per-board selector override |
| C14 | A WAF returns 403 when the User-Agent contains `HeadlessChrome` | Closed — a plausible UA at every launch site |
| C15 | A native `<select>` filter whose CDP node dies on the SPA's rerender mid-action | Worked around per board with a URL pre-filter; the CDP interaction itself is unfixed |
| C16 | A personalized "featured" block shares origin, prefix, depth, and URL shape with the real results | Closed — `LinkRule.suppress_ancestor_selector` |
| C17 | Responsive dual-render: anchors exist only in the mobile-only block that the visibility gate correctly drops | Closed — `hooks.pre_extract_css` |
| C18 | The location filter is single-select per URL with no aggregate option covering the region | Closed — `pre_filter_urls` union |
| C19 | Fingerprint-based bot management rejects any non-browser HTTP client regardless of headers | **Partly closed** — where the gated response lands in readable page state, borrow it instead of re-issuing the request (`CoveoConfig.browser_token_key`, SYS-19). Still **open** where the needed data never reaches such state |
| C20 | **Exclusive** accordion: opening one section closes the others, so no DOM state ever holds the full listing — and each section may carry its own cap | **Open** — needs a nested multi-state walk; neither `expand_selector` nor the pagination walker expresses it |
| C21 | Apply-decoy: the board's only buttons matching the Case B `Search/Apply/Submit/Filter` vocabulary are job-application affordances (`Apply Here`, `View and Apply`), and clicking one leaves the listing page | Closed — Case B anti-scope guard in `navigation/prompt.py` naming the job-application labels and stating no click is needed when selecting the option already updates the listings |
| C22 | Hash-routed SPA: postings are well-formed `<a>`, but the job id lives **only** in the URL fragment (`#/jobs/5050`), which the matcher strips by design — every posting collapses to the board root | **Open** — needs a third link shape (id-in-fragment) with its own prefix semantics; no config knob reaches it |

Three open classes sit **below every strategy**: C1 and C22 on the DOM path
and C20 in the DOM state machine. None is reachable by a prompt change; C22 is
reachable by a matcher change, but needs new prefix semantics, not a knob. C2
remains open outside Greenhouse, and C19 is open only in its residual form (see
the transport frontier).

No open class now sits at the **LLM seam**. The former "C4 multi-accordion
residual" was a misdiagnosis — see the Deel entry — and C11 closed by deleting
the agent from the path rather than by persuading it.

**Note on `filter_already_applied` (bears on C11 and C3).** The prompt-side hook
holds on some boards and silently fails on others, and board platform does not
predict which. Measured failures, three runs each: doola (8/1/1 without the hook,
1/6/4 with it), Sapphire Labs (0/10 — Case C fired on a decoy filter offering
only "All" and a typo'd "Headquater"), CSC Generation (7/465/7 — a cleared Lever
facet exposing the whole board), AireSpring (12/0/12), LSEG (20/7/7 — Workday's
unfiltered page size), Progress (4/1/1). Measured holds: Cirtec Medical, DXC
Technology, Cohesity, AmEx GBT, TOMIA, Babel. Workday and Lever each appear on
both sides, and DXC holds despite carrying more region-facet chrome than any
board in the corpus, so chrome presence is not the discriminator either. Treat
"URL carries the filter **and** the page renders filter chrome" as presumed
unstable: reach for `pre_filter_urls` first, and keep the hook only where three
runs prove it holds.

## Blocked companies

### Luflox — C1, open

*Investigated 2026-07-02 (round 1), re-confirmed 2026-07-16; not re-investigated
since. Queue: `blocked-companies.json`, `expected_jobs: 3`.*

An Angular SPA at `https://www.luflox.com/career`, hydrated from a Firestore
`Listen/channel` long-poll against the `luflox-management-prod` project. Each
posting is a `<mat-card>` whose click handler calls
`router.navigate(['/career/details', <id>])` imperatively. No descendant is an
`<a href>`, and no attribute anywhere in the subtree carries the destination.
The DOM stabilises in ~6s and stays stable, so this is not a wait-timing
problem.

The profiler reports zero anchors under every candidate prefix, no frames, no
shadow roots, no pagination affordance, no filter controls, and no platform
fingerprint. Every matcher widening shipped since round 1 stays on the wrong
side of the pipeline's foundational assumption: *a job must be reachable via an
`<a href>` present in the DOM at query time.*

**Why it stays blocked.** The strategy port is the right seam, but neither
available route is cheap. A Firestore adapter would need the customer's Cloud
project id, collection path, id field, and URL template — none derivable from
`job_board_url`, so every Firestore board would ship as bespoke config rather
than a one-line opt-in. A generic click-scanning strategy would need per-site
candidate heuristics (cookie banners, logos, and modal triggers all look
clickable), fragile back-navigation timing, and its own stateful regression
harness, since it is a sequenced interaction rather than a one-shot evaluate.

**Disposition (2026-08-15).** Reviewed and deliberately left blocked. Three
postings do not justify either route, and the next C1 board is unlikely to be
Firestore, so the config surface would not be reused. A second C1 board arrived
2026-09-14 (Instacredit, below) on a different transport with the destination
present in the DOM — a Firestore surface would still not be reused, so this
disposition stands.

### Instacredit — C1 (form-submit variant), open

*Investigated 2026-09-14. Queue: `blocked-companies.json`, `expected_jobs: 11`.*

A WordPress board at `https://empleos.instacredit.com/empleos/`. Cards come from
`POST /wp-admin/admin-ajax.php` (`action=fetch_empleos`, params `page`, `pais`,
`cargo_categoria`, `localidad`), which returns a JSON envelope around an HTML
fragment. Every posting navigates by form submission, not an anchor:

```html
<form action="/v_empleo" method="get">
  <input type="hidden" name="id" value="336">
  <input type="hidden" name="nombre" value="Gestor de cobranza telefónica …">
  <button type="submit" class="apply-button">Aplicar</button>
</form>
```

Zero `<a>` under any prefix, on all 3 pages and in every AJAX payload.
`extractor_ground_truth.py` returns 0 with the corrected prefix `/v_empleo` (the
queue's query-form `sample_job_url` derives `/`; the path form `/v_empleo/314/`
is required). Unlike Luflox, the destination *is* in the DOM: `action` plus the
hidden inputs fully determine `/v_empleo?id=336&nombre=…`.

**Three independent gaps**, each measured on the live board:

1. **Link shape (C1).** A form-derived-link rule prototyped in-page synthesizes
   9/9 links per page with 0 false positives — the filter form falls outside the
   prefix on its own. Closes this gap only.
2. **Pagination (C13 shape).** `<button class="page-next">→</button>`, AJAX
   swap, URL unchanged. Closable today with `paginate=True` and
   `hooks.next_control_selector="#pagination .page-next"`.
3. **Location filter — the real wall.** `select#pais` values are exact
   `country, city` strings and the backend matches exactly: `pais=Costa Rica`
   returns 0. Costa Rica is split across four options (San José 7, `Costa Rica,`
   1, Cartago 1, Alajuela 1 = 10). Filter state never reaches the URL, so
   `pre_filter_urls` cannot express it. A union of four exclusive selections is
   the C20 shape.

```text
today                                0
+ form rule                          9   (page 1)
+ form rule + pagination            23   (whole board — verdict `over`)
Costa Rica, four-way union          10   (target 11)
```

**Target drift.** Live Costa Rica count is 10 against `expected_jobs: 11`.
Posting 312 carries a blank city (`Costa Rica,`) and is easy to miss or
double-count. Needs a human re-count before any route can verify.

**Options.** (a) An adapter over the admin-ajax endpoint: four POSTs with the
four `pais` values, parse cards, synthesize URLs from the hidden inputs. Closes
all three gaps with no agent and no browser; the fixture is a recorded payload
(C12 pattern). Cost: an HTML-parsing strategy whose config would not generalize
past this board. (b) A generic form-derived-link matcher rule behind an opt-in
`LinkRule` flag. Reusable — it closes the form variant of C1 for any future
board — but alone leaves this one at 23 / 11. Build (b) for a board it actually
completes.

### HNM Systems — C22, open

*Investigated 2026-09-15. Queue: `new-companies.json`, `expected_jobs: 1` —
no agreed plan yet; move to `blocked-companies.json` if not pursued.*

An AngularJS hash-routed career portal at `https://hnmsystems.com/openjobs/`,
backed by Bullhorn (`public-rest33.bullhornstaffing.com/rest-services/1D1609/
search/JobOrder`, public, no auth). Postings **are** well-formed anchors — the
failure is the URL shape, not the element type:

```html
<a href="#/jobs/5050">Senior Project Manager</a>   <!-- x20, ids differ -->
```

The matcher strips the fragment before bucketing (`linkUrl.hash = ''`), which
is correct for `#apply`-style in-page anchors and exactly wrong here: the
fragment *is* the job identity. All 20 anchors normalize to the single href
`https://hnmsystems.com/openjobs/`, and the prefix test then fails — with
`basePath="/"` the `startsWith("//")` check is false, and with
`basePath="/openjobs"` the id-in-query branch needs a query string, which a
hash route does not have.

Measured, filtered to the one Costa Rica posting (`Costa Rica, Heredia`):

```text
job anchors in DOM                       1   (href="#/jobs/5050", the right job)
after the matcher's hash strip           https://hnmsystems.com/openjobs/
extractor, path_prefix="/openjobs"       0
extractor, derived prefix "/"            0
```

The target itself is sound: the Bullhorn API reports 149 open jobs, of which
exactly **1** is Costa Rica (id 5050, Senior Project Manager, Heredia).

**Why config alone cannot fix it.** No knob reaches a fragment. `path_prefix`
and `min_depth` operate on `pathname`, which is `/openjobs` for every posting;
`pre_filter_urls` is also out because filter state never reaches the URL — the
location facet leaves the address at `#/jobs`.

**Why it is not C1.** The anchors are real and carry the destination. This is
a normalization collision, one layer below the element-type assumption that
defines C1.

**Options.** (a) A third link shape, *id-in-fragment*, alongside id-in-path and
id-in-query: keep route-like fragments (`#/…`) instead of stripping them, and
match the fragment route against a fragment-derived prefix. The discriminator
is clean — `#apply` is an in-page anchor, `#/jobs/5050` is a hash-mode router
route — and both shapes that pin today's collapse behaviour
(`test_linkrule_parity.py::test_parity_fragment_collapse_dedups`,
`test_probe_census.py::test_fragment_variants_collapsed`) use `#apply`-style
fragments, so they would keep passing. Cost: new prefix semantics, since
`derive_path_prefix` reads `pathname` and would need a fragment-route
counterpart, plus depth handling to separate `#/jobs/5050` from the `#/jobs`
listing route. (b) A Bullhorn adapter — the REST endpoint is public, unauthed,
and returns the full job list with addresses, so the region filter is a
server-side query. Reusable across any Bullhorn tenant, with `corpToken` as
the only per-board config.

**Disposition.** One posting does not justify either route today. Bullhorn
Career Portal is a widely deployed product, so revisit when a second C22 or
Bullhorn board arrives — (b) is the cheaper of the two and generalizes by
tenant, while (a) is the one that closes the class.

### Deel — C20, reclassified from C4; blocked

*Investigated 2026-07-11 and 2026-07-17; re-investigated in depth 2026-08-15.
Queue: `blocked-companies.json`, `expected_jobs: 6`.*

A React SPA whose postings are well-formed `<a href="/careers/job/?ats_id=…">`
anchors behind department accordions labelled `<department> N open roles`. The
board has grown since the first investigation: **15 departments, 261 roles
claimed**, against a 6-posting Costa Rica target.

The 2026-08-15 investigation overturned the previous diagnosis. Three findings,
each independently disqualifying a proposed mitigation.

**1. The accordion is *exclusive*.** Opening one section closes the others, so
no DOM state ever contains the whole listing. Driving `expand_all`'s model —
click every collapsed header until none remain — does not converge; it
oscillates indefinitely:

```text
round 1: 15 collapsed -> clicked 15, anchors now 20
round 2: 14 collapsed -> clicked 14, anchors now 4
round 3: 14 collapsed -> clicked 14, anchors now 20     (alternates thereafter)
```

This retires the previous entry's claim that `hooks.expand_selector` was an
available-but-unverified closure. It is **verified as inapplicable**. The
selector itself is fine and was re-validated the same day:
`button[aria-expanded="false"][class*="hover:bg-neutral-50"]` matches exactly
the 15 role-count headers, where the naive `button[aria-expanded="false"]`
also catches 9 cookie-consent, language, and filter buttons.

**The earlier LLM-seam diagnosis was wrong.** The previous entry attributed the
failure to the agent "not iterating the full accordion set", supported by seven
runs across three model tiers returning 1, 1, 1 / 0 / 0, 3, 0. Those results are
now explained by the board, not the model: only one section can be open, so a
single extract can only ever see one department. The differing `ats_id`s between
runs — read then as the agent picking arbitrarily — are just whichever section
happened to be open. No prompt or model change was ever going to close this.

**2. The location filter is not URL-addressable.** Four candidate parameters
each returned the unfiltered 261:

```text
?location=Costa%20Rica   ?locations=Costa%20Rica
?country=Costa%20Rica    ?office=Costa%20Rica
```

The `All locations` control is a custom dropdown that did not apply under either
a synthetic `.click()` or a real Playwright click in the investigation harness.
So `pre_filter_urls` — the closure that unblocked Progress the same day — has no
URL to carry here, and the region target is unreachable by the deterministic
path regardless of the accordion problem.

**3. Sections carry their own cap.** Opening each of the 15 sections in turn and
unioning the per-state matcher results reaches **157, not 261**: every
department with more than 20 roles is itself truncated at 20 (Engineering 29→20,
Payroll 47→20, Sales 88→20). So the board is a *two-level* state machine —
exclusive sections, each with its own load-more.

**What today's mechanisms actually return.** No configuration produces a stable,
meaningful number:

```text
single-shot (all collapsed)                            0
hooks.expand_selector                                 20   (oscillates with 4)
paginate + next_control_selector on the headers       15
per-section manual union (ceiling, not a mechanism)  157   of 261 claimed
```

**Why it stays blocked.** Closing it needs a nested multi-state driver —
enumerate N mutually-exclusive sections × paginate within each — which no
current mechanism expresses. The pagination walker is single-level and assumes
monotonic advance toward an end state; `expand_all` assumes sections are
independently openable. And even a working nested walk yields whole-board 261,
not the 6-posting region target, because finding 2 leaves no way to filter. That
is a new subsystem plus an unsolved filter, for one board.

## Open architectural frontiers

**Non-anchor boards (C1).** The pipeline assumes a job is reachable via an
`<a href>` in the DOM. Where a backing API exists and is reachable, an adapter
closes the board by construction. Where the data plane is per-tenant
configuration (Luflox's Firestore) or the only route is interaction, the cost is
a new strategy plus either a config surface far larger than any existing field
or a stateful sequenced-interaction harness with per-site heuristics. Neither is
a cheap extension.

Instacredit adds a third sub-shape that *is* cheap on the matcher side: the
destination is fully present in the DOM as a GET `<form action>` plus hidden
inputs, so a generic form-derived-link rule (opt-in `LinkRule` flag, synthesized
URL fed through the existing prefix buckets) would close the link-shape half
with no per-board config. It is deliberately not built yet because the only
board of the shape is also blocked by a non-URL-addressable, four-way-split
location filter (C20 shape), so the rule would not complete it. Build it when a
form-submit board arrives that it finishes.

**Nested DOM state machines (C20).** Both multi-state mechanisms in the codebase
assume a *single* level: the pagination walker advances monotonically toward an
end state, and `expand_all` assumes sections open independently. Deel needs
both at once — enumerate mutually-exclusive sections, paginate within each — and
neither composes into the other. This is the frontier most likely to be worth
building if a second board of the shape appears, because unlike C1 it needs no
new config surface: the driver would be generic and the per-board input is still
just a selector. One board does not justify it.

**The LLM seam.** No open class sits here any more, but the finding stands and
governs how the remaining frontiers should be built. Where the deterministic
half is correct and the agent's decision-making fails, it fails *silently*, with
`agent_completed=true`. The architectural answer is to move the failing step off
the seam, and C11's closure is the strongest precedent: not a more emphatic
prompt, but deleting the agent from the path via `pre_filter_urls`. The two
tiers differ in kind — a *prompt-side* hook only asks the agent to behave and
demonstrably may not be obeyed, whereas an *agent-less* path makes the failure
structurally unreachable. Prefer the latter. Deel is the cautionary case in the
other direction: seven runs across three model tiers were read as an agent
decision problem for a month, when the board simply could not present the data.

**Driving inputs (C2).** The agent can discover and click controls; it has no
instruction for *typing* into one, even where a text search is the only region
affordance and the resulting DOM is ideal for the matcher. Deliberately not
built, on measured grounds recorded here because the board that motivated it
(Vintti) shipped whole-board on 2026-08-15 and its entry is gone.

The capability needs four conditions to hold at once — no location facet, a
search box, the search indexing the text that carries the region, and cards that
actually name it — and of three measured boards it pays off on one: Vintti
filtered 50→9, Hire With Near returned nothing (its cards are LATAM-wide and
never name a country), and Cohesity returned 0 because its search matches titles
only. There is no way to detect that conjunction short of testing each board by
hand.

The mechanism is also more fragile than a click hook. On Cohesity, setting the
input value and dispatching synthetic `input`/`keyup` events did nothing — the
framework responded only to real per-character keystrokes — and a deterministic
fill hook would fire through the same `evaluate` seam. Compare the capture
driver's click fallback, where the *opposite* asymmetry held (a JS click worked
where a real mouse click timed out): input events are framework-specific in a
way clicks are not.

Finally, on the motivating board the filtered number was arguably the wrong
target: 33 of Vintti's 51 cards read "Any Country" and are roles a Costa Rica
candidate is eligible for, so filtering on the literal string would have
silently dropped them. If several boards accumulate where search-typing is the
only route *and* whole-board is unacceptable, that is the evidence to build a
deterministic fill hook — a hook, not a prompt clause, per the LLM-seam
findings above.

**Transport (C19) — partly closed 2026-08-15.** Fingerprint-based bot
management defeats every non-browser client regardless of headers, and is
invisible to any investigation conducted through a browser. The hypothesis this
section held in reserve turned out to be right, and is now shipped: *read what
the page already minted; never re-issue the gated request.*

The asymmetry is sharper than expected and is the whole mechanism. On UST all
three of these are true at once:

```text
httpx GET of the mint URL                                    403
page.evaluate fetch of the SAME URL, inside the loaded page  403
reading sessionStorage['searchToken_en_us'] after load       works
```

So "use a browser" is not the fix — a browser *re-issuing* the request is
refused exactly like `httpx`. Only the **state read** crosses.
`CoveoConfig.browser_token_key` encodes that, and UST ships on it (three runs,
20 / 20 / 20).

What remains open is the residual. This works only where the gated response
lands somewhere readable. A tenant holding its token in a closure, a service
worker, or memory the page never persists would still be blocked, and the
mitigation would have to move to response interception — a heavier seam that
reads traffic rather than storage. No board needs that yet.

The transferable rule for intake: when a gate blocks an adapter, check what the
page **already has** before concluding the data is unreachable.

## How to add an entry

Record the investigation date, the pattern class (adding a new class from the
next free number only when no existing class fits), and the specific DOM or
network evidence quoted from the live board — keep examples short. State what
was tried and why each attempt failed, in enough detail that someone can tell
whether a later architectural change would close it. Close with the queue
disposition and the options considered, each with its architectural cost.

Re-check an entry's claims before relying on them: Deel's mitigation was
recorded as available for a month and was disproven on first contact, and its
diagnosis was wrong in a way that pointed at the model instead of the board.
Prefer evidence produced by the mechanism itself — a measured count from the
real code path — over reasoning about what should happen.

Delete the entry when the board ships.
