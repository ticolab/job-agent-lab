---
name: integrate-company
description: Integrate one company into job-discovery's COMPANIES catalog and verify the saved live report against onboarding-only expected_jobs queue evidence. Use when the user names a company to integrate, when debugging an integration count, or when troubleshooting zero, short, or over-count extraction. Read name, aliases, job_board_url, sample_job_url, and expected_jobs from new-companies.json; never copy expected_jobs into Company or runtime data.
---

# Integrate One Company into the Job Discovery Corpus

Integrate **one** company into the `job-discovery` corpus, then externally
verify that the saved live report contains the exact number of job listings
recorded as queue evidence. Iterate — add the company, run it, debug failures —
until the observed report count matches the evidence. On success, remove the
company's entry from the tracked `new-companies.json` queue and commit the
integration (config + snapshot + queue workflow change) in a single commit; on
a blocker, stop and report without committing.

## Objective

Append **one** company to `COMPANIES` in
`src/job_discovery/catalog/companies.py`. Keep the queue's `expected_jobs` only
in working context, run the live integration, and verify its saved JSON report
externally. "Done" means the live report has no error and reports exactly the
queue count, the unfiltered regression snapshot is captured, the corpus passes,
the integrated queue entry is removed, and one `feat:` commit passes hooks.
Then notify and wait for instructions. Never push.

One invocation = one company. Do not batch.

## Inputs

Expect to be invoked with a **single company name** — the canonical `name`,
an alias, or any substring. The integration target lives in the tracked
`new-companies.json` queue at the repo root (schema documented by
`new-companies.example.json`; see `README.md` → "Adding a new company"). Resolve
the supplied name against the queue by case-insensitive substring match on
`name`, or exact match against any entry in `aliases`. After resolving exactly
one entry, use its canonical `name` verbatim as `QUEUE_NAME` in the final check.

Each entry has five fields:

```json
{
  "name": "<Company Name>",
  "aliases": [],
  "job_board_url": "https://<board-host>/<org>",
  "sample_job_url": "https://<board-host>/<org>/<some-posting-id>",
  "expected_jobs": null
}
```

**Refuse to start, and tell the user what is wrong**, in any of the following
cases:

- `new-companies.json` does not exist at the repo root. Tell the user that the
  tracked queue is missing and to restore it with `git restore new-companies.json`.
- No entry in the queue matches the supplied name.
- More than one entry matches (ambiguous name — ask the user to pick).
- The matched entry has placeholder values: `name` or either URL contains a
  `<` or `>` character.
- `expected_jobs` is not a nonnegative JSON integer. Reject `null`, booleans,
  strings, floats, and negative integers, and name the invalid field.

Treat the four `Company`-shaped fields (`name`, `aliases`, `job_board_url`,
`sample_job_url`) as a **starting proposal**, not gospel. Part of the job is
to discover whether `job_board_url` points at the real board and whether
`sample_job_url` yields a correct path prefix (see below). When the evidence
shows they're wrong — for example, the queue supplies a marketing-site URL but
the actual board is on a different host — **fix the value only in
`catalog/companies.py`**, and call the correction out in the commit body.
Never write the correction back to `new-companies.json`; that file is removed
wholesale at step 8 anyway, and `catalog/companies.py` plus git history are
the durable record.

`expected_jobs` is immutable onboarding-only queue evidence. Keep the validated
integer in working context for the final external report check. Never add it to
a `Company(...)` entry, runtime report, database row, prompt, or snapshot
metadata. Never adjust it to match observed reality; disagreement is a blocker
or requires the user to update the queue evidence.

## Bundled resources

- `scripts/extractor_ground_truth.py` — Appendix B in script form. Runs the real
  extractor against the rendered DOM, no LLM. Invoke it from the repo root so
  `job_discovery` is importable:
  ```bash
  uv run python <skill-root>/scripts/extractor_ground_truth.py
      --job-board-url <url> --sample-job-url <url> [--wait 10] [--scroll 3]
  ```
  Replace `<skill-root>` with the directory containing this `SKILL.md`.

## Required reading (do this first)

Before touching anything, read:

- `CLAUDE.md` — project overview and conventions.
- `src/job_discovery/catalog/companies.py` — the `COMPANIES` list; the
  `Company` / `LinkRule` pydantic models live in `src/job_discovery/domain/company.py`.
- `src/job_discovery/extraction/dom/agent/prompt.py` — the shared `GOAL_PROMPT` the
  live agent runs under.
- `src/job_discovery/extraction/dom/rules.py` — `derive_path_prefix()`, and
  `src/job_discovery/extraction/dom/assets/collect_links.js` — the matcher JS
  body (registered as the `extract_job_links` tool in
  `src/job_discovery/extraction/dom/agent/controller.py`). **Understand these two
  before debugging.**

## How the system works (mechanics to understand)

**Run a single company:**
```bash
uv run job-agent-lab -c <handle>     # handle = an alias, the name acronym, or a name substring
```
Requires `OPENAI_API_KEY` in `.env`. Output is written to
`output/<slug>_<timestamp>.json` (gitignored) and a summary is printed. The
number that matters is `metadata.total_jobs_found` (shown as `Jobs:` in the
summary).

**The extraction is deterministic, the navigation is not.** A `browser-use`
agent (gpt-4o-mini) drives the browser — it navigates, optionally applies a
location filter, and scrolls. That part is **non-deterministic** and can wander
or hit step limits. The actual link collection is a **deterministic** JS tool
(`extract_job_links`) that runs in the page. Keep this distinction front of
mind: **a wrong count can come from either the extractor config OR a flaky
agent run.** Isolate which (see Debugging).

**How valid job links are identified.** `extract_job_links`:

1. Derives a **path prefix** from `sample_job_url` via `derive_path_prefix()` =
   `posixpath.dirname(<url path, trailing slash stripped>)`.
2. Derives an **origin** from `job_board_url` (`scheme://host`).
3. Collects every `<a>` on the rendered page that is **same-origin** AND matches
   the prefix in one of two shapes:
   - **id in the path** — `linkPath.startsWith(prefix + "/")`
     (e.g. prefix `/jobs` matches `/jobs/7540236-senior-eng`).
   - **id in the query string** — `linkPath === prefix && link has a query`
     (e.g. prefix `/careers/requirements` matches
     `/careers/requirements/?pId=180`).
4. Deduplicates by full `href`.

**Consequences to respect:**

- The **path prefix is everything.** Verify it before anything else:
  ```bash
  uv run python -c "from job_discovery.extraction.dom.rules import derive_path_prefix; print(derive_path_prefix('<sample_job_url>'))"
  ```
  Choose `sample_job_url` so this prints the prefix shared by all real job
  links. For query-string sites, give the **path form** that yields the right
  prefix — e.g. Akurey's `/careers/requirements/271/` → `/careers/requirements`.
  The query form `/careers/requirements/?pId=271` would derive `/careers` (too
  broad) and over-match.
- **Same-origin only.** If the real jobs live on a different host (an embedded
  Greenhouse/Lever/recruitcrm board), `job_board_url` must point at **that
  host**, not the company's marketing page. (See the Golabs example.)
- **Only `<a>` tags are collected.** Sites that render listings as `<button>` /
  `<div onclick=...>` won't be matched — that's a structural limitation, not a
  config error (flag it; don't hack).
- **The tool refuses on a posting page.** If the agent has clicked into a
  single job and calls `extract_job_links` there, the tool returns an error
  telling it to return to the listing instead of the page's one self-link
  (`rules.is_posting_url`; see Deloitte, where that produced a plausible 1).
  A live run showing that refusal in its log is the agent wandering, not a
  config problem — `pre_filter_urls` removes the agent and the risk together.
- Already handled (do not re-add): Chromium launches with
  `--password-store=basic --use-mock-keychain` (no macOS keychain prompt), and
  the built-in LLM judge is disabled (`use_judge=False`). Ignore any
  `Page readiness timeout` / `bubus` / watchdog warnings — they're noise.

## Playwright MCP: live investigation

The Playwright MCP tools drive a real browser interactively from this session.
Use them as the primary surface for understanding what a target site actually
does — before and during running the extractor ground-truth diagnostic. MCP
does **not** replace the diagnostic, which exercises the real extractor code
path and remains the ground truth for whether the config is correct.

Reach for MCP whenever a question about the live page cannot be answered from
static HTML: what host the job links sit on, whether listings are `<a>` or
buttons/divs, whether a hidden XHR returns the job list as JSON, whether a
filter or pagination control changes the count, whether the board is served
from an iframe on a different origin, or whether there is an anti-bot wall or
login gate.

The tools that pay back the most per call are summarised below. Full names
begin with `mcp__playwright__` (e.g. `mcp__playwright__browser_navigate`);
recipes use the shorthand form.

| Tool | Use it to |
|---|---|
| `browser_navigate` | Load the `job_board_url` (or a candidate replacement) in a real browser. |
| `browser_snapshot` | Get the accessibility tree to see actual element types (`<a>` vs `<button>`), iframes, and structure. |
| `browser_evaluate` | Run JS in the page to count or inspect anchors, dedupe hrefs, scroll programmatically. |
| `browser_network_requests` | List XHR/fetch calls — often the fastest way to find an internal jobs API returning JSON. |
| `browser_network_request` | Fetch the full body of a specific request found via `browser_network_requests`. |
| `browser_console_messages` | Surface JS errors that may explain why listings never render. |
| `browser_click` / `browser_wait_for` | Apply a filter, click "Load more", change category, then re-count. |
| `browser_take_screenshot` | Capture a login wall, captcha, or empty state for the blocker report. |

### Recipes

Each recipe answers one debugging question. Run the tools in order; insert
`browser_wait_for` between actions that trigger network calls.

***Example: count jobs the way a human would.*** This counts every same-origin
anchor whose path matches your candidate prefix, giving a fast sanity check
before running the extractor diagnostic. Call `browser_evaluate` with this
function after `browser_navigate`:

```js
() => {
  const PREFIX = "/jobs";              // your candidate prefix
  const ORIGIN = location.origin;
  const links = [...document.querySelectorAll('a[href]')]
    .map(a => new URL(a.href, ORIGIN))
    .filter(u => u.origin === ORIGIN)
    .filter(u => u.pathname.startsWith(PREFIX + "/") ||
                 (u.pathname === PREFIX && u.search.length > 0));
  const unique = [...new Set(links.map(u => u.href))];
  return { count: unique.length, sample: unique.slice(0, 5) };
}
```

***Example: find a hidden jobs API.*** Many career sites render listings from
an internal JSON endpoint; finding it gives the true count without DOM
heuristics. The sequence is `browser_navigate`, then a short wait, then list
and inspect matching XHRs:

```text
1. browser_navigate         -> <job_board_url>
2. browser_wait_for         (time: 4)
3. browser_network_requests (filter: "jobs|careers|positions|openings|requisitions")
4. browser_network_request  (index: N, part: "response-body")
```

If the response is JSON with a job array, its length is the authoritative
human-equivalent count, and `job_board_url` should point at the host serving
that XHR.

***Example: detect cross-origin or iframe-hosted boards.*** This tells whether
the real board lives on a different host (Greenhouse, Lever, recruitcrm.io)
embedded into the company's marketing page:

```js
() => ({
  iframes: [...document.querySelectorAll('iframe')].map(f => f.src),
  hosts: Object.entries(
    [...document.querySelectorAll('a[href]')].reduce((m, a) => {
      const h = new URL(a.href, location.origin).host;
      m[h] = (m[h] || 0) + 1;
      return m;
    }, {})
  ).sort((a, b) => b[1] - a[1]).slice(0, 10)
})
```

If a non-company host dominates the histogram, point `job_board_url` at that
host (or at the iframe `src`).

***Example: confirm listings are `<a>` and not buttons/divs.*** A single
snapshot is enough. If listing rows render as `button` or `generic` nodes with
`cursor=pointer` and no link, the extractor cannot match them — that is a
structural limitation to report, not a config bug. Run `browser_navigate`
followed by `browser_snapshot` and inspect the listing region.

***Example: test a filter or load-more before automating.*** This isolates
whether a filter the browser-use agent is applying shrinks the count below
`expected_jobs`. Use `browser_snapshot` to find the filter control's ref, then:

```text
1. browser_evaluate  (count recipe above)  -> baseline
2. browser_click     (target: <filter ref>)
3. browser_wait_for  (time: 2)
4. browser_evaluate  (count recipe again)  -> compare
```

## Workflow

0. **Probe the board first** (reconnaissance, one browser render, no LLM,
   no cost). Run the board profiler before touching config:

   ```bash
   uv run python scripts/probe_board.py \
       -u "<job_board_url>" \
       -s "<sample_job_url>"
   ```

   Read the seven-section report against the blocker classes documented
   in `blockers/INTEGRATION_BLOCKERS.md`. Section 1 (anchor census per
   candidate prefix) shows whether the derived prefix reaches the
   expected count and whether a wider ancestor's depth histogram points
   at a `min_depth` fix (C9). Section 2 (frame map) shows same-origin
   frames the runtime matcher will reach in-page and cross-origin
   frames it cannot (C5). Sections 4 through 6 surface pagination
   affordances (C7), filter controls the agent will need to drive (C2,
   C3), and platform fingerprints — a Greenhouse fingerprint routes the
   integration through `strategy="greenhouse"` and the API-payload path
   instead of a DOM snapshot. Section 7 assembles a suggested
   `Company(...)` snippet.

   The probe renders the board once and drives nothing — filters are
   inventoried, not applied, and accordions are not expanded, so C3 and
   C4 boards surface as filter-census findings rather than post-filter
   counts. Every subsequent step in this workflow (prefix check, ground-
   truth extraction, live run, snapshot capture, quality gates, commit)
   remains mandatory. The section-7 snippet is a starting point that
   Step 3's ground-truth script and Step 5's live run verify — never
   copy it into `catalog/companies.py` without running those checks.

1. **Verify the prefix** with the `derive_path_prefix` one-liner above. If it
   doesn't look like the common stem of the site's job URLs — or the site
   hasn't been seen yet — open it in Playwright MCP first (`browser_navigate`
   plus the *count jobs the way a human would* recipe with the candidate
   prefix). That is faster than guessing. Gather evidence in step 3 before
   settling it.

2. **Add the company** to the `COMPANIES` list in `catalog/companies.py`
   (append; do not duplicate an existing entry). Keep `aliases` lowercase
   and short. Add only `Company` fields supported by the domain model.
   `expected_jobs` stays in working context as queue evidence and must not
   appear in the `Company(...)` entry.

3. **Measure unfiltered ground truth first (no LLM, no cost).** Run the bundled
   script against the real rendered listing:
   ```bash
   uv run python <skill-root>/scripts/extractor_ground_truth.py
       --job-board-url <url> --sample-job-url <url>
   ```
   Use this result only as the expected count for the unfiltered snapshot in
   step 6. Do not use it to validate queue `expected_jobs`, even when the two
   values happen to coincide.
   If the result is structurally wrong, debug in step 4. For lazy-loaded sites,
   retry with `--wait 12 --scroll 4` or measured higher values.

4. **Debug** until the deterministic result accurately measures the unfiltered
   listing that will be frozen.
   Reach for Playwright MCP first on any failure — it is faster than writing
   throwaway scripts and answers most "what does the page actually do"
   questions in one or two tool calls.

5. **Confirm end-to-end** with one live integration:
   ```bash
   uv run job-discovery integrate -c <handle>
   ```
   Capture the exact path printed on the `Output:` line; do not select an
   `output/*.json` file by glob or modification time. Machine-check that report
   against the queue evidence, replacing both placeholders. `QUEUE_NAME` must
   be the exact canonical `name` from the uniquely resolved queue entry. Never
   accept or derive the expected count from an `EXPECTED_JOBS` environment
   variable or any manually supplied value:
   ```bash
   REPORT_PATH='<exact Output path>' QUEUE_NAME='<exact queue company name>' \
   uv run python - <<'PY'
   import json
   import os
   from pathlib import Path

   queue = json.loads(Path("new-companies.json").read_text())
   matches = [entry for entry in queue if entry.get("name") == os.environ["QUEUE_NAME"]]
   assert len(matches) == 1, "QUEUE_NAME must select exactly one queue entry"
   expected = matches[0].get("expected_jobs")
   assert type(expected) is int and expected >= 0, (
       "expected_jobs must be a nonnegative JSON integer"
   )

   report = json.loads(Path(os.environ["REPORT_PATH"]).read_text())
   metadata = report["metadata"]
   jobs = report["jobs"]
   assert metadata["error"] is None
   assert metadata["total_jobs_found"] == len(jobs) == expected
   completed = metadata["agent_completed"]
   if metadata["strategy"] == "dom" and "states_visited" not in metadata:
       assert completed is True
   else:
       assert completed is None
   PY
   ```
   This strict `type(...) is int` check rejects booleans, nulls, strings,
   floats, and negative integers before the same raw queue value is used in the
   report comparison. API and agent-less runs legitimately use
   `agent_completed: null`; an actual DOM-agent run must report `true`. If
   deterministic ground truth is accurate
   but the live report is short or zero, re-run up to three times. Persistent
   disagreement is a blocker.

6. **Capture a regression snapshot.** Freeze the rendered HTML so the matcher's
   current behaviour on this site is locked in and any future change to the
   matcher JS or the extraction/navigation subpackages is bisectable against
   the whole corpus:
   ```bash
   uv run python scripts/capture_snapshot.py -c <handle> --expected <N>
   ```
   `<N>` is only the **unfiltered** ground-truth count from step 3. Never use
   queue `expected_jobs` merely because the values happen to coincide. The
   snapshot test runs the matcher against the unfiltered listing, while queue
   evidence validates the saved live report externally. The snapshot is written
   to `tests/fixtures/snapshots/<slug>/` (`page.html` + `metadata.json`).

7. **Run quality gates.** All must pass:
   ```bash
   uv run ruff format . && uv run ruff check .
   uv run mypy
   uv run --group test pytest tests/
   ```
   `pytest` exercises the entire snapshot corpus — the one you just added and
   every previously-integrated company. If a matcher-touching change (anywhere
   under `extraction/`, which now covers the agent wiring too) regressed an
   older company, this is where you find out.

8. **Remove the company from the queue.** The repo root carries the tracked
   `new-companies.json` file that lists the remaining integration backlog.
   Delete the entry for the company you just integrated so the queue stays in
   sync with what is actually in `catalog/companies.py`. This is part of the
   integration change and enters git.

9. **Commit the integration.** Stage the `catalog/companies.py` edit, the new
   `tests/fixtures/snapshots/<slug>/` directory, and the tracked
   `new-companies.json` queue removal. Leave unrelated changes unstaged. Use a
   lowercase `feat:` subject (`feat: add <Name> to company test sample`) —
   subject line only, 100 characters max, no body and no trailers, per
   `CLAUDE.md`. The ATS, any cross-origin / prefix gotchas, the live agent run
   numbers, and the snapshot path belong in the report below, not in the commit
   message. The pre-commit hooks (run `pre-commit install` once per clone if not
   yet installed) will fire ruff, mypy, end-of-file-fixer, and the
   snapshot-regression pytest hook. If `end-of-file-fixer` or any other
   auto-fixer modifies a file, re-stage and retry — do **not** bypass with
   `--no-verify`. After the commit, run `git status` to confirm the working tree
   is clean, then report (format below). Never push.

## Debugging playbook (symptom → likely cause → fix)

| Symptom | Likely cause | First MCP probe | Fix |
|---|---|---|---|
| **0 jobs**, anchors exist on page | Wrong path prefix, or job links are on a **different origin** than `job_board_url` | `browser_evaluate` the host-histogram recipe; if a non-company host dominates, the board is cross-origin | Fix `sample_job_url` (to correct the prefix) and/or `job_board_url` (to the real board host). |
| **0 jobs**, page shows listings | Links are **query-string style** but prefix derived too deep, OR jobs aren't `<a>` tags | `browser_snapshot` — if listing rows are `button`/`generic` with no link, they aren't `<a>`; otherwise `browser_evaluate` to inspect pathnames and search strings | If query-style, pick a `sample_job_url` whose path equals the listing prefix. If they're buttons/divs, report as a structural limitation. |
| **0 jobs**, MCP shows few/no anchors | Jobs are **JS-rendered or lazy-loaded** and hadn't loaded | `browser_wait_for` then `browser_evaluate` to scroll; check `browser_network_requests` for the XHR fetching the listings | If an XHR returns the job JSON, point `job_board_url` at the host serving that XHR. Otherwise re-run the script with higher `--wait` and `--scroll`. |
| **Fewer than expected** | Pagination, lazy-load needing more scroll, or a location filter narrowed results | `browser_snapshot` for a "next page" / "load more" control; `browser_click` it and re-run the count recipe | Re-run the script with more `--scroll`. If a location filter is being applied by the browser-use agent and shrinking the set, note it (`GOAL_PROMPT` filtering is optional). |
| **More than expected** | Prefix too broad (catches category/nav/"apply" links), or **tracking-param duplicates** of the same job counted twice | `browser_evaluate` to compare distinct pathnames against distinct full hrefs — duplicates show up as different `?utm_*` but identical path | Narrow the prefix. If duplicates differ only by tracking params, that is a real dedup gap — report it. |
| Count correct from script but **wrong in full run** | Non-deterministic browser-use agent (didn't extract, didn't scroll, hit step limit) | Watch with `uv run job-agent-lab -c <handle> --headed`; MCP does not help here (the issue is in the in-process agent, not the page) | Re-run a few times. Report if it stays flaky. |

**Tools at your disposal, in rough order of reach.** **Playwright MCP** is the
primary live-investigation surface — see the recipes above. The bundled
**`scripts/extractor_ground_truth.py`** is the deterministic ground truth: it
runs the real extractor over the real rendered DOM with no LLM cost.
**`curl -sL <url>`** returns raw HTML only and will not surface JS-rendered
listings, but it is useful for static boards and for comparing what MCP
renders against the raw server response. **`uv run job-agent-lab -c <handle>
--headed`** shows the full agent run in a visible browser when end-to-end is
flaky despite a correct script result. Temporary `logger`/`print` statements
in the extraction (`extraction/dom/`) or navigation (`extraction/dom/agent/`) modules
are fine while debugging — remove them before finishing.

**Capture-time flags for stateful boards.** Two `scripts/capture_snapshot.py`
flags exist for boards whose full anchor set is not present in a single
post-render, post-scroll DOM state; each has a runtime counterpart in the
matcher / `GOAL_PROMPT` that must be in sync with it.

Reach for `--paginate` (SYS-5) when the board renders one page of listings
at a time behind a `Next` control (anchor, ARIA-labelled button, text-labelled
button, or numeric cursor). The flag walks the pager on the capture side and
freezes each state as `pages/page-N.html` under the snapshot directory, and
the harness unions per-state matcher runs. It is only meaningful when the
`Company` entry has `Company.paginate=True`, which routes the runtime
matcher through the same walker in `extraction/dom/collector.py`. The two
must match: `paginate=True` catalog entry pairs with `--paginate` at capture,
`paginate=False` (the default) pairs with the unflagged capture. Techwarely
and BCG are the two shipped precedents.

Reach for `--expand-selector CSS` (SYS-6) when the filtered listings are
grouped into collapsed accordions whose per-anchor DOM is only mounted once
the section header is clicked (Deel's role-count headers are the canonical
shape). The flag clicks every visible match in ≤5 bounded rounds after the
render+scroll settle and before the SYS-2 bake, so the frozen `page.html`
contains the mounted anchors. The runtime counterpart is the `GOAL_PROMPT`
C4 clause (STEP 2 preamble: "click each such header first to reveal its
listings" for headers labelled `N open roles`/`N positions`/`N jobs`/`Expand`/
`Show` under Cases A and B, excluding FAQ/cookie/footer accordions) — the
agent expands the same headers itself at runtime; the flag only exists so
the frozen fixture reflects the post-expansion DOM. `--paginate` and
`--expand-selector` are mutually exclusive at the argparse layer (no
corpus board today needs both, and the interaction is untested).
Verify a candidate selector with a `browser_evaluate` count-histogram
before running the capture — on Deel the naive `button[aria-expanded="false"]`
also catches nine cookie/filter/language buttons, whereas the refined
`button[aria-expanded="false"][class*="hover:bg-neutral-50"]` isolates the
fifteen role-count accordions cleanly (selector re-validated 2026-07-17;
see the Deel entry in `blockers/INTEGRATION_BLOCKERS.md`).

**Slow-hydrating boards need a settle, not more retries.** `--wait` /
`--scroll` are for *dialling in* a value; a board that permanently needs
more than the 8s global records it in the catalog as
`hooks.render_wait_sec` / `hooks.render_scroll_count`, so the runtime and
every future capture use it automatically and the two cannot drift. The
schema restricts those fields to entries with non-empty `pre_filter_urls`
— the agent-less runner is the only path that applies a settle, so an
override on an agent-driven board would move the frozen fixture without
moving the runtime. Measure before guessing: poll the page once per second
for anchors rather than bisecting `--wait`, because the failure looks
identical (a confident zero) at every value below the real hydration time.
Edwards Lifesciences is the motivating board — zero anchors at 8s, 12s,
20s and 30s, first anchor at 46-50s across three trials, so it ships
`render_wait_sec=60`. A capture at a raised settle records it in
`metadata.render_wait_sec` for provenance, because such a fixture cannot
be re-derived by re-running at the default.

SYS-6 also shipped a C3 prompt clause that lets the agent discover bare
native `<select>` filter elements (by `name`/`id`/associated-label matching
tokens like `country`/`location`/`region`/`office`/`city`/`pais`/`país`),
recognise Costa Rica by two-letter or three-letter ISO code (`CR`, `CRI`)
in Case B, and click a `Search`/`Apply`/`Submit` button when the filter sits
inside a `<form>`. This retires the URL-pre-filter workaround pattern
(previously used for Sequoia Connect's `?countryDD=CR` and still used for
Confiz's `?country=CR` where a Select2-overlay apply-semantics gap keeps the
workaround in place) as a default for freshly-integrated `<select>`-filter
boards. See the SYS-6 resolution notes in `blockers/INTEGRATION_BLOCKERS.md`
under Sequoia Connect and Deel for the full context, the trace evidence,
and the pattern classes that are now closed vs still open.

## Constraints & guardrails

- **Commit exactly once, only on success, never push, never branch.** When all
  quality gates and the live agent run pass (steps 5–7), step 9 commits the
  integration in a single `feat:` commit covering `catalog/companies.py`, the
  new `tests/fixtures/snapshots/<slug>/` directory, and the tracked queue-entry
  removal from `new-companies.json`, subject line only, no body. Do not stage
  unrelated changes. On a blocker, do **not** commit at all; leave changes in
  the working tree for human review. Never `git push`, never create branches,
  never bypass pre-commit with `--no-verify`.
- **Prefer config-level fixes** (`sample_job_url`, `job_board_url`, `aliases`,
  `link_rule.path_prefix`) over code changes. They solve the vast majority of
  cases.
- **Only edit the matcher JS (`extraction/dom/assets/collect_links.js`) if a
  genuinely new, general link-shape pattern** is found — and make it
  **generic**. Never hardcode a company name, a fixed job-id list, or a
  site-specific branch into the extractor. (Per `CLAUDE.md`: the link
  extraction is deterministic; do not replace it with LLM-guessed lists.)
- **Playwright MCP is for investigation only.** Do not import MCP-driven logic
  into any production module (`extraction/`, `catalog/`, or `domain/`). Do not
  declare the task Done based on MCP
  findings alone — the bundled script and a real `uv run job-agent-lab -c
  <handle>` remain the gates. Do not commit MCP artefacts (`.playwright-mcp/`,
  screenshots, console-message dumps); keep them outside the repo.
- **Snapshots capture the unfiltered listing only.** Do not modify
  `scripts/capture_snapshot.py` to apply a location filter (or any other
  filter) before capture — mixing filtered and unfiltered snapshots in the
  corpus makes regression failures impossible to localise. The location-filter
  behaviour is the live agent's responsibility and is exercised by step 5, not
  by the snapshot test. Unlike `.playwright-mcp/` artefacts, the snapshot
  directory under `tests/fixtures/snapshots/<slug>/` **is** meant to be
  committed alongside the company's `catalog/companies.py` entry.
- **Never fake the result.** The count must come from the real extractor on
  the real site, not a hand-written list.
- Delete any throwaway diagnostic scripts / debug prints added before
  reporting. (Put temp scripts outside the repo or in a path that won't be
  committed.)
- Follow repo conventions: Python 3.12+, Ruff (line-length 88, double quotes),
  Mypy; imports as `from job_discovery.X`.

## Definition of Done

All of the following hold:

1. The company is appended to `COMPANIES` in `catalog/companies.py` without
   `expected_jobs`; the validated nonnegative queue integer remains only in
   working context.
2. The bundled `extractor_ground_truth.py` result is used only as the unfiltered
   snapshot expected count.
3. A live `uv run job-discovery integrate -c <handle>` run's exact `Output:` report path
   is captured and machine-checked: `metadata.error is None` and
   `metadata.total_jobs_found == len(jobs) ==` queue `expected_jobs`. API and
   agent-less `agent_completed: null` is valid; a DOM-agent run requires `true`.
4. A regression snapshot exists at `tests/fixtures/snapshots/<slug>/`
   (`page.html` + `metadata.json`), and
   `uv run --group test pytest tests/` passes against the whole corpus.
5. If any code was changed: `ruff format`, `ruff check`, and
   `mypy` all pass, and the change is generic (no
   site-specific hacks).
6. The integrated company's entry has been removed from the tracked
   `new-companies.json` queue file at the repo root.
7. A single `feat: add <Name> to company test sample` commit has been created,
   staging `catalog/companies.py`, the new
   `tests/fixtures/snapshots/<slug>/` directory, and the queue-entry removal,
   subject line only with no body or trailers; all pre-commit hooks passed
   (ruff, mypy, end-of-file-fixer, snapshot regression). Nothing was pushed.
8. Temporary debug artifacts (throwaway scripts, `print`s, MCP dumps)
   removed.

If the target cannot be reached after a genuine effort (e.g. jobs are
cross-origin in a way the current design can't express, non-`<a>` listings, a
login wall, or anti-bot blocking), **stop and report the blocker** with
evidence and a recommendation — do not force a number.

## Reporting format

When done (or blocked), output a concise report.

**On success:**
```
✅ Integrated: <name>
   handle used:     -c <handle>
   job_board_url:   <final value>
   sample_job_url:  <final value>
   derived prefix:  <prefix>
   queue evidence:  expected_jobs=<N> (external only)
   extracted jobs:  <N>   (live report: <exact Output path>)
   snapshot:        tests/fixtures/snapshots/<slug>/  (unfiltered: <M>)
   corpus tests:    all passed
   code changed:    none | <file + 1-line why, generic>
   commit:          <short-hash> <subject>
   queue:           new-companies.json now has <K> entries (was <K+1>)
Awaiting further instructions.
```

**On blocker:**
```
⚠️ Blocked: <name> — got <actual> / <expected>
   root cause:   <what was found, with evidence>
   tried:        <briefly>
   why blocked:  <why config alone can't fix it>
   recommendation: <smallest generic change, or info needed>
Awaiting further instructions.
```

## Worked examples (the three companies already in `catalog/companies.py`)

| Company | `sample_job_url` | derived prefix | link shape | note |
|---|---|---|---|---|
| Growth Acceleration Partners | `.../jobs/7540236-senior-python-aws-software-engineer` | `/jobs` | id in **path** | board on company's own host |
| Akurey | `.../careers/requirements/271/` | `/careers/requirements` | id in **query** (`?pId=180`) | path-form sample chosen so prefix is right; real links are query-style |
| Golabs Tech | `https://recruitcrm.io/apply/17760976583210110395Pmz` | `/apply` | id in **path** | `job_board_url` is the **ATS host** (`recruitcrm.io`), not golabs.com |

These illustrate the three things that most often need adjusting: path vs.
query shape, picking the sample so the prefix is correct, and pointing
`job_board_url` at the host that actually serves the jobs.
