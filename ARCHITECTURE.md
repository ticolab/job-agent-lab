# Architecture

`vacantes` answers one question a few times a week: what job postings are open
right now. It extracts job-posting URLs from company career sites. This
document is the big-picture map: the layers, the seams between them, and the
rules that keep the design from decaying as new sites arrive.

Per-feature semantics and the change history live in `TABNINE.md`. Per-board
evidence — the catalogue of site behaviours that resist extraction, with the
ground truth behind each — lives under `blockers/`. Forward-looking design
work lives in `TRANSITION.md`, which is transient: each phase's section is
deleted once it lands and its durable rationale graduates into this document.

## The central split

One decision shapes everything else:

> **Navigation is non-deterministic. Extraction is deterministic.**

A `browser-use` agent drives the page: it loads the board, finds and applies a
location filter, reveals lazily-mounted content, expands collapsed sections.
That work resists specification — every site does it differently — so an LLM
absorbs the variance. But the agent never decides *what counts as a job link*.
That judgement belongs to a JavaScript matcher that runs inside the page and
returns a URL list.

The two halves have opposite failure modes and opposite testing needs. Agent
behaviour is probabilistic and can only be validated against a live site.
Matcher behaviour is a pure function of a DOM, so it can be frozen, replayed,
and regression-tested offline across the whole corpus in seconds. Keeping them
apart means a wrong count is always attributable to one side. Collapsing them —
letting the model report the links it believes it saw — would make every count
unauditable, which is the failure this project exists to avoid.

## Components and layering

The second structural decision is where code is allowed to point. Three
components sit over one shared kernel: `extraction/` owns the port and every
strategy, `persistence/` owns the database, and `batch/` owns concurrency and
the definition of one company's unit of work. The kernel — `domain/`,
`catalog/`, `settings.py` — is the vocabulary all three speak. All three
exist today, alongside `cli/` and `reporting/`, and both entry points are
wired: one command onboards a single board, the other drives the corpus
and is the only thing in the system that writes to the database.

Dependencies point inward and never cycle:

```
Enforced today by tests/unit/test_layering.py:

  cli         → batch, catalog, domain, extraction, persistence, reporting,
                settings
  batch       → domain, extraction, persistence
  extraction  → domain, settings
  persistence → domain, settings
  reporting   → catalog
  catalog     → domain
  settings    → (nothing)
  domain      → (nothing inside vacantes)
```

Two of those rows came out narrower than the design anticipated, and
both for one reason: a component that is *handed* what it needs does not
import it. `persistence` reaches neither `catalog` nor anything above
it, because the catalog is the source of truth for the corpus and the
database only ever projects it, so the projection is handed the
companies to write. `batch` reaches neither `catalog` nor `settings`,
because the scheduler is handed both the companies to run and the
factory that opens database sessions, and its ceilings are policy rather
than shared runtime configuration. `reporting` stays out of `batch`
because rendering belongs to the command that drives a batch, not to the
batch itself.

One row came out *wider* than the design anticipated, and it is the same
principle viewed from the opposite end. The batch command reaches
`persistence` as well as `batch`, because something has to decide where
the database lives, open the engine, and hand the session factory
inward — and that job belongs to the outermost layer. The command is the
composition root, and its two edges are precisely *why* the scheduler
below it needs no route to shared configuration and never imports a
global engine. The inversion shows up as an extra edge here and as a
missing one there; the two observations are one fact. Reading it instead
as a leak would invite the opposite fix — moving engine construction
inward, down to whatever needs a session — which is the change that
would actually break the graph, because it is what would let a strategy
reach the database.

This graph is not housekeeping. Two entry points share one extraction core:
the integration CLI drives one board at a time and produces the JSON artifact a
human reviews before a catalog entry is committed, while the batch scheduler
runs the corpus concurrently and persists the result. Both call the same
`extract` coroutine with the same `RunContext`. The integration workflow is
therefore a correctness signal for scheduled runs — but only for as long as a
strategy cannot tell which caller invoked it. A strategy that could reach the
database, or detect that a batch was in progress, could behave differently
under the scheduler than under the CLI, and the onboarding evidence would stop
meaning anything.

`tests/unit/test_layering.py` parses the imports of every module under
`src/vacantes/` and asserts each package imports only from its allowed set,
which makes the constraint structural rather than a matter of review
discipline. The allowlist is written from the real import graph rather than
from intent, and it may only ever shrink. Widening a row — say
`extraction → catalog` — is therefore a deliberate allowlist edit that
surfaces in review, never something a new import does silently. That
`extraction` may reach neither `persistence` nor `batch` is asserted by
name as well as by table, so the invariant survives a future edit to the
allowlist.

## Extraction strategies

Every run dispatches through one port in `extraction/base.py`: an
`ExtractionStrategy` protocol with a single `extract(company, ctx)` method, a
frozen `RunContext` (model, headless, step cap, target region), and
`build_report(...)` — the sole author of the output shape. Because every
strategy reports through `build_report`, the JSON and terminal summary are
identical in shape no matter how the jobs were found; strategies that never
run an agent emit `None` for the four agent-only fields, rendered as `n/a`.

Two families implement the port.

The **DOM strategy** is the default and covers most of the corpus: agent plus
in-page matcher, as described above.

**API adapters** bypass the browser entirely, querying the ATS or search
platform that backs the board and filtering the returned records by region.
Each is one module under `extraction/ats/` with a per-tenant config block on
the company entry. A board earns an adapter when the DOM path cannot yield an
honest regression artifact or cannot reach the postings at all — three
recurring reasons:

- **Filter state is not URL-addressable.** The region filter lives in
  component state or a request body, so a captured page cannot be replayed in
  its filtered form.
- **The unfiltered board dwarfs the pagination cap.** Any frozen count would
  be an artifact of where the walker stopped rather than a property of the
  board, and would break on every posting turnover.
- **There are no anchors.** A client-rendered board may expose postings only
  as fields in a search response, in which case no matcher extension reaches
  them.

Adding a strategy means extending the name literal in `domain/company.py`
*and* registering the implementation in `extraction/__init__.py`. A unit test
asserts the two sets are equal, so shipping half the change fails loudly. A
registered strategy may legitimately have no corpus entry — the code can be
ready before any board using it is reachable.

## The deterministic matcher

The matcher lives in one file, `extraction/dom/assets/collect_links.js`,
loaded once at import time. The runtime collector, the snapshot capture
script, the regression suite, and the board profiler all execute that same
source. There is no second copy to drift.

**What it scans.** From the top document it descends into every open shadow
root and every same-origin frame it can reach, guarded so that inaccessible
frames are skipped rather than fatal. Closed shadow roots and cross-origin
frames are structurally opaque and stay that way. Each surviving anchor is
gated on CSS visibility, so content the user cannot see does not count.

**How it decides.** An anchor must be same-origin and match one of two URL
shapes relative to a path prefix: the id sits in the path
(`/jobs/12345-engineer`), or the id sits in the query on the prefix itself
(`/careers/requirements/?pId=180`). Fragments are stripped before
deduplication. When both shapes appear on one page the path bucket wins and
the query bucket is discarded, because a query on a listing root is usually a
filter facet rather than a posting; the query bucket is consulted only when
the path bucket comes out empty. That single rule is why choosing the prefix
correctly matters more than any other piece of per-company configuration.

**Declarative narrowing.** Boards that need more discrimination get it through
data, never through a site-specific code branch. A company's link rule can
override the derived path prefix, require a minimum path depth (separating
real postings from shallower marketing chrome under a shared root), and
suppress anchors inside a named container (excluding a "featured" or
"recommended" block whose anchors are indistinguishable from real results at
the URL layer). Each field defaults to a value that leaves matcher behaviour
byte-identical, so adding one is additive rather than a behaviour change.

## Reaching the full listing

Not every board presents its whole listing in one DOM state, so three
mechanisms sit between page load and matcher invocation. All are opt-in per
company and inert by default.

**Pagination.** A walker collects the first state, discovers a next-page
control through a priority-ordered cascade of generic signals (link
relationship, accessible label, visible text, numeric successor, class token,
load-more affordance), clicks it, waits for the collected URL set to change,
then re-collects — unioning across states. It terminates when no control is
found, when a click yields nothing new, when settling times out, or at a hard
page cap. The cascade is deliberately ordered so that semantic signals
outrank cosmetic ones.

**Multi-URL union.** Some boards express a region only as several distinct
URLs — one per city or office — with no single URL covering the target. Such a
company declares those URLs and the runner visits each in turn, unioning the
results. This path runs no agent at all and consumes no API key.

**Page preparation hooks.** Four narrow knobs pin a board to the
deterministic side when the agent fails it reproducibly: inject a stylesheet
before extraction (to reveal anchors hidden by a layout-class rule), click a
repeated expand affordance in bounded rounds, override next-control discovery
with an explicit selector, or — the only prompt-side hook — tell the agent the
filter is already applied via the URL and it must not touch any filter
control. That last one exists because re-applying an already-applied filter
can toggle it *off*, silently converting a filtered board into the full one.

Everything that touches the live page does so through a small `PageDriver`
protocol (evaluate, click, and friends), which is what lets the same collector
code serve the runtime's browser-use session and the capture script's raw
Playwright page without either knowing about the other.

## Region as data

The target region is a single object holding two surfaces: the filter labels
an agent may click, and a predicate that decides whether a structured location
string belongs to the region. The prompt renders the first; the API adapters
apply the second. One definition, so the DOM and API paths cannot disagree
about what the region means.

## Verifying a run

A company entry carries a human-counted, region-filtered target. The report
layer compares it against what was found and emits a verdict —
`match`, `under`, `over`, or `unverified` when no target has been counted. A
CLI flag turns a non-match into a non-zero exit.

Two properties matter. The target is **never** adjusted to match observed
output; if the two disagree, either the configuration is wrong or the board
moved, and both deserve a human decision. And the target is never interpolated
into any agent-visible text, so the model cannot curve-fit toward the number
it is being scored against.

The verdict layer is also the designated detector for configuration rot. A
selector, facet id, or pre-filter URL that stops working degrades to a lower
count on the next live run rather than failing silently.

## Persisting the result

A batch's output is a database rather than a pile of JSON artifacts:
SQLite behind SQLAlchemy's async layer, with three tables — `companies`,
`company_runs`, and `job_urls`. Four decisions in that shape are
structural rather than incidental.

**What is open and whether we looked are separate tables.** `job_urls`
answers the first question and `company_runs` the second. Their
lifetimes differ — the URL set is replaced wholesale on every success
while the run history accumulates — and a single `updated_at` spanning
both would make a failed run indistinguishable from a successful one. A
board that broke this morning would look done this afternoon and would
silently stop being retried. `companies` is the third table and a pure
projection of the catalog, refreshed at the start of every batch so the
database is self-describing; it is never an owner.

**Freshness keys on successes, and crash recovery falls out of that
rule.** The skip decision asks whether a company *succeeded* recently,
not whether it was attempted, which keeps failures eligible for retry
and successes from being repeated inside the window. A batch killed
mid-run leaves rows still marked in progress; the next batch reaps them
to failed, and because they are not successes the affected companies
re-run while genuinely-completed ones are skipped. Resumption is
therefore not a separate mechanism, and there is no checkpoint file to
keep correct. The staleness bound the reap measures against is the
per-company timeout, on the grounds that no run may legitimately
outlive its own ceiling.

Two consequences of that rule are worth separating out, because both
were settled by killing a live batch rather than by reasoning. Recovery
does not actually *depend* on the reap: an abandoned row is not a
success, so the company is eligible again the moment the next batch
looks at it, and the reap is only the bookkeeping that closes the
orphaned row once the bound has passed. A run can therefore be retried
before it has been reaped at all. And a kill does not invalidate work
that had already finished — a company whose previous run succeeded
inside the window is correctly skipped immediately after a crash,
because nothing is owed for it. Both follow from asking only about
successes, which is the property that makes the rule worth keeping
narrow.

**A successful extraction replaces the URL set wholesale** — delete and
insert inside one transaction, with no history kept by design. That is
simpler than upsert-plus-prune and, unlike it, cannot leave a stale
posting behind, because there is no "which rows did I not see this
time" bookkeeping to get wrong. The transaction is the failure
boundary: a crash before, during, or after leaves either the previous
snapshot or the new one and never a partial set, so the database stays
safe to query while a batch is writing.

**Workers write directly, with no queue between them and the
database.** A queue plus a single writer earns its complexity when
write throughput saturates the database, when writes need batching, or
when several processes need coordinated ordering, and none of those
holds here: each company produces one transaction of a few dozen rows
after minutes of extraction, so the database is idle almost all of the
time. Every statement lives in one of three repository modules, which
is what makes the decision reversible — if contention ever appeared, a
queue would slot in behind the unchanged repository interface rather
than rippling out into the strategies or the scheduler.

## Running a batch

The batch is three modules with one responsibility each: one decides
whether and how expensively a company runs, one defines what a single
company's unit of work means, and one decides when. Together they are a
semaphore and a gather rather than a workflow framework, which is the
right weight for one operator, one machine, and units of work with no
dependencies between them. Four decisions in that shape are structural.

**Concurrency is classified by cost, not by strategy name.** The
classifier takes a company rather than a strategy identifier, because
splitting on the name gets exactly one case wrong: a Coveo board
configured to read its search token out of page state opens a real
browser to do so, and therefore costs a Chromium context despite being
an API strategy. Twenty of those in the cheap bucket would exhaust
memory. A test asserts every registered strategy has a declared class,
so a new adapter cannot ship without someone deciding what it costs.
Classification deliberately does not live on the strategy protocol:
widening that protocol would make every existing strategy
non-conforming, and it would hand a strategy knowledge of the scheduler
that the layering rules exist to deny it.

**The two ceilings are independent semaphores.** A browser run holds a
Chromium context and, on the DOM path, an LLM agent bounded by provider
rate limits; an HTTP run is a JSON call bounded only by politeness. One
shared ceiling would make the cheap bucket queue behind the expensive
one, which is the opposite of what a ceiling is for.

**The worker is the failure-isolation boundary.** Failure modes across a
corpus of third-party sites are open-ended — a crashed browser, a
provider rate limit, DNS, a redesign that breaks a selector — so the
contract is that no single board aborts the batch. Every board-level
exception is caught there and written to the run history for an operator
to read, while interrupt and cancellation still propagate so Ctrl-C
behaves normally. An extraction returning zero jobs is a **successful
run with an unhappy verdict**, never a failure: marking it failed would
put honestly empty boards in the retry path forever and hide real
breakage among them. Crash recovery needs nothing of its own here — the
scheduler reaps abandoned runs before any company starts, and the rest
follows from the freshness rule above.

**The scheduler is handed its inputs.** It receives the companies to run
and the factory that opens database sessions rather than importing the
catalog or a global engine. That is what holds its layering row to three
entries, and it is what makes the component testable: a fake strategy
and a temporary database exercise the real scheduler, worker, and
repositories with no browser and no network in the loop.

Two further decisions belong to the command that drives a batch rather
than to the batch itself, and both fall out of the isolation boundary
above.

**The exit code reports whether the batch ran, not whether every board
succeeded.** A board-level failure is already data, recorded in the run
history with its error text, so a batch that reached the end exits zero
even when boards inside it failed. A non-zero exit is reserved for the
batch being unable to start at all: a handle that resolves to nothing,
a selection that empties out, a database with no schema. Folding board
health into the exit code would contradict the contract that a
third-party failure is an ordinary event, and the signal would stop
carrying information the first week a site went down. The single-board
command behaves the same way, exiting zero on an unhappy verdict unless
it is explicitly asked to be strict, so neither entry point teaches a
different habit than the other.

**The aggregate summary is rendered by the command, not by the
reporting layer.** Per-company JSON still routes through the one module
that authors that artifact, so the output format keeps exactly one
writer. But an aggregate summary reads the batch's own result object,
and teaching the reporting layer to understand the batch package would
invert a dependency to buy a symmetry that nothing needs.

## The regression model

Two corpora, deliberately separate.

**Frozen DOM fixtures.** Every DOM-strategy company contributes a captured
copy of its rendered listing page plus metadata recording the expected count.
Multi-state boards store one file per state. The suite loads each document
into a real Chromium page under its original base href, runs the production
matcher, and asserts the union matches the recorded count. Capture bakes
visibility decisions and open shadow roots into the serialized HTML so a
JavaScript-disabled replay reaches the same verdict the live matcher did.

**Recorded API payloads.** Every API-strategy company contributes a captured
response, replayed through a mocked HTTP layer. An API-strategy company
deliberately has no page fixture — substituting the payload *is* the point,
since the reason it took the API path was that no honest page fixture exists.

Fixtures freeze the **unfiltered** listing, not the region-filtered target.
This is the same separation of concerns as the central split: the fixture
validates the matcher's counting, the live run validates the agent's
filtering. Conflating them would produce failures that localise to neither. A
board whose configuration suppresses a container is the one case that must be
captured *with* suppression active — the suppressed sections are typically
personalized and drift independently of the real results, so freezing one
would guarantee a flaky count.

Where a URL rule is mirrored outside the JavaScript matcher — an API adapter
that must filter anchors the same way — a parity test runs both engines over
the same input and asserts identical output, so the mirror cannot silently
diverge. Pre-commit gates the whole suite on any change under the extraction or
domain packages, or anywhere under `tests/`.

## Integrating a board

Each new company follows a fixed pipeline, and every stage is a gate:

1. **Profile** the board read-only — anchor census per candidate prefix, frame
   and shadow map, pagination affordances, filter controls, platform
   fingerprints. Nothing is driven and nothing is written.
2. **Settle the prefix**, then confirm the count deterministically with no
   agent in the loop.
3. **Run live** and require a `match` verdict.
4. **Freeze** the fixture — page snapshot or recorded payload.
5. **Run the gates**: format, lint, types, and the full corpus.
6. **Commit once**, config plus fixture together.

The profiler's suggested configuration is a starting point, never applied
without the later stages confirming it. Failure at any stage is reported as a
blocker with evidence rather than worked around by loosening the target.

## Invariants

These are the rules that keep the above coherent. Breaking one is how this
design would decay.

- The matcher is the only thing that decides what a job link is. The model
  never supplies URLs.
- Per-board differences live in configuration, not in code branches. No
  company name, host, or posting id appears in the matcher.
- Dependencies point inward. `extraction` never imports `persistence` or
  `batch`, so a strategy cannot tell which caller invoked it and cannot behave
  differently under the scheduler than under the integration CLI.
- No single board aborts a batch. A third-party failure becomes a
  recorded outcome, and an empty board is a success with an unhappy
  verdict rather than a failure to retry. The exit code follows from
  that: it reports whether the batch ran, not whether every board in it
  succeeded.
- The matcher JavaScript has exactly one copy on disk.
- Every opt-in knob defaults to inert, so adding one cannot change any
  existing board's behaviour.
- Every emitted URL is read from the page or payload, not reconstructed —
  except where a platform genuinely publishes no posting URL, in which case
  the synthesis must be verified against live responses before it ships.
- Fixtures capture the unfiltered board; filtering is the live run's business.
- The human-counted target is immovable.

## Alternatives considered

The regression harness came first, deliberately, before any restructuring of
the matcher. It was the enabling investment: with the corpus in place, every
later change to extraction could be validated across every previously working
board in seconds, which is what made the rest safe to attempt.

A **per-ATS adapter registry** keyed on hostname was the original plan for
handling recurring platforms. It was deferred until enough integrations had
accumulated to show which families actually recur, then generalised on
arrival: dispatch is per *data source* rather than per hostname, which is
strictly more expressive — it covers platforms that answer over HTTP as well
as those that merely share a URL convention, and it leaves the DOM path as the
default rather than a fallback.

A **per-company URL regex** was rejected as the primary mechanism. It taxes
every integration with authoring and maintaining a pattern even where the
default derivation works, and it moves the extraction logic into data that no
test can meaningfully constrain. The declarative link-rule fields are its
replacement for the cases that genuinely need more expressiveness: they are
narrow, individually testable, and inert unless set.
