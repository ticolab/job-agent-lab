---
name: integrate-company
description: Integrate a single company into job-agent-lab's COMPANIES config and iterate until the deterministic extractor returns the human-counted expected_jobs target. Use when the user names a company they want integrated into the lab — the full entry (name, aliases, job_board_url, sample_job_url, expected_jobs) is read from new-companies.json at the repo root, which the user populates from the committed new-companies.example.json template. Also use when debugging why an already-configured company returns the wrong job count, or troubleshooting zero / short / over-count extraction at integration time.
---

# Integrate One Company into the Job Agent Lab

Integrate **one** company into the `job-agent-lab` test sample so that the lab
extracts the **exact number of job listings** that a human counted on that
company's career site. Iterate — add the company, run it, debug failures —
until the extracted count matches the target. On success, remove the
company's entry from the `new-companies.json` queue and commit the
integration (config + snapshot) in a single commit; on a blocker, stop and
report without committing.

## Objective

Append **one** company (provided in the input) to `COMPANIES` in
`src/job_agent_lab/catalog/companies.py`, then make the extractor return the
**expected number of jobs**. "Done" means the deterministic extractor returns
exactly that count on the real site, with no errors, the regression snapshot
is captured and the corpus passes, the integrated entry is removed from the
queue, and a single `feat:` commit lands cleanly through the pre-commit hooks.
Then notify and wait for instructions. Never push.

One invocation = one company. Do not batch.

## Inputs

Expect to be invoked with a **single company name** — the canonical `name`,
an alias, or any substring. The integration target lives in `new-companies.json`
at the repo root, an untracked, personal backlog file the user maintains by
copying the committed `new-companies.example.json` template (see
`README.md` → "Adding a new company"). Resolve the supplied name against the
queue by case-insensitive substring match on `name`, or exact match against
any entry in `aliases`.

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

- `new-companies.json` does not exist at the repo root. Tell the user to
  `cp new-companies.example.json new-companies.json` and add their entry.
- No entry in the queue matches the supplied name.
- More than one entry matches (ambiguous name — ask the user to pick).
- The matched entry has placeholder values: `name` or either URL contains a
  `<` or `>` character, or `expected_jobs` is `null`. Name the missing field.

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

`expected_jobs` is the immovable target. The deterministic extractor must
return exactly that number on the real site, or the run is a blocker. Never
adjust the target to match observed reality — if the count is wrong, the
config is wrong (or the site has genuinely drifted and the user needs to
update the entry).

## Bundled resources

- `scripts/extractor_ground_truth.py` — Appendix B in script form. Runs the real
  extractor against the rendered DOM, no LLM. Invoke it from the repo root so
  `job_agent_lab` is importable:
  ```bash
  uv run python <skill-root>/scripts/extractor_ground_truth.py
      --job-board-url <url> --sample-job-url <url> [--wait 10] [--scroll 3]
  ```
  Replace `<skill-root>` with the directory containing this `SKILL.md`.

## Required reading (do this first)

Before touching anything, read:

- `CLAUDE.md` — project overview and conventions.
- `src/job_agent_lab/catalog/companies.py` — the `COMPANIES` list; the
  `Company` / `LinkRule` pydantic models live in `src/job_agent_lab/domain/company.py`.
- `src/job_agent_lab/navigation/prompt.py` — the shared `GOAL_PROMPT` the
  live agent runs under.
- `src/job_agent_lab/extraction/dom/rules.py` — `derive_path_prefix()`, and
  `src/job_agent_lab/extraction/dom/assets/collect_links.js` — the matcher JS
  body (registered as the `extract_job_links` tool in
  `src/job_agent_lab/navigation/controller.py`). **Understand these two
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
  uv run python -c "from job_agent_lab.extraction.dom.rules import derive_path_prefix; print(derive_path_prefix('<sample_job_url>'))"
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
begin with `mcp_playwright_`; recipes use the shorthand form.

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
   and short. Set the `expected_jobs` field to the exact value from the
   queue entry (SYS-9 `Company.expected_jobs`; the queue schema in
   `new-companies.example.json` and `blocked-companies.example.json`
   already carries this human-counted target). Never adjust the value
   to match observed reality — if the number is wrong, the config is
   wrong, and Step 5's verdict gate will surface it.

3. **Validate deterministically first (no LLM, no cost).** Run the bundled
   extractor ground-truth script. It runs the **real** extractor against the
   **real rendered DOM**, bypassing the flaky LLM agent:
   ```bash
   uv run python <skill-root>/scripts/extractor_ground_truth.py
       --job-board-url <url> --sample-job-url <url>
   ```
   - If it prints `EXTRACTOR RETURNED: <expected_jobs>` → the config is
     correct; go to step 5.
   - If not → debug (step 4). Use the Playwright MCP recipes above to see the
     actual link shapes, hosts, and counts in the live page. For lazy-loaded
     sites, retry the script with `--wait 12 --scroll 4` (or higher).

4. **Debug** using the playbook below until the script returns `expected_jobs`.
   Reach for Playwright MCP first on any failure — it is faster than writing
   throwaway scripts and answers most "what does the page actually do"
   questions in one or two tool calls.

5. **Confirm end-to-end** with one real run:
   ```bash
   uv run job-agent-lab -c <handle>
   ```
   Read the `Verdict:` line from the printed summary (or
   `metadata.verdict` in the `output/*.json` file). It must be
   `match`, with `agent_completed: true`. SYS-9 authors the
   comparison inside `build_report`, so this is a machine check,
   not a human eyeball against `total_jobs_found` — the four
   possible verdicts are `match`, `under`, `over`, and `unverified`.
   Under `--strict` (opt-in), anything other than `match` exits
   non-zero after the loop drains.
   - A `match` line annotated with `(agent reported step errors)`
     is landable but warrants a look at the run log first: the
     count is right *and* the DOM agent hit a non-fatal step
     error along the way, which is often a benign transient but
     is worth confirming before commit.
   - If the script matched but the full run is short or zero, that's **agent
     flakiness** (didn't reach extraction, didn't scroll enough, etc.) —
     re-run up to ~3 times. Persistent end-to-end failure despite a correct
     script result is itself a finding worth reporting.

6. **Capture a regression snapshot.** Freeze the rendered HTML so the matcher's
   current behaviour on this site is locked in and any future change to the
   matcher JS or the extraction/navigation subpackages is bisectable against
   the whole corpus:
   ```bash
   uv run python scripts/capture_snapshot.py -c <handle> --expected <N>
   ```
   `<N>` is the count `extractor_ground_truth.py` returned in step 3 — i.e. the
   **unfiltered** count. For sites with an on-page location filter (Atmosera,
   Zencore, etc.) this is **not** the same as `expected_jobs`; it is the
   larger, pre-filter number, because the snapshot test runs the matcher
   against the unfiltered listing. For sites without an on-page location
   filter, `<N>` equals `expected_jobs`. The snapshot is written to
   `tests/fixtures/snapshots/<slug>/` (`page.html` + `metadata.json`).

7. **Run quality gates.** All must pass:
   ```bash
   uv run ruff format . && uv run ruff check .
   uv run mypy src scripts tests
   uv run --group test pytest tests/
   ```
   `pytest` exercises the entire snapshot corpus — the one you just added and
   every previously-integrated company. If a matcher-touching change (anywhere
   under `extraction/`, `navigation/`, or the `agent.py` shim) regressed an
   older company, this is where you find out.

8. **Remove the company from the queue.** The repo root carries an untracked
   `new-companies.json` file that lists the remaining integration backlog.
   Delete the entry for the company you just integrated so the queue stays in
   sync with what is actually in `catalog/companies.py`. The file stays
   untracked — this edit does not enter git.

9. **Commit the integration.** Stage **only** the `catalog/companies.py` edit
   and the new `tests/fixtures/snapshots/<slug>/` directory; leave
   `new-companies.json` and `ARCHITECTURE.md` untracked. Use a lowercase
   `feat:` subject (`feat: add <Name> to company test sample`) — subject line
   only, 100 characters max, no body and no trailers, per `TABNINE.md`. The
   ATS, any cross-origin / prefix gotchas, the live agent run numbers, and
   the snapshot path belong in the report below, not in the commit message.
   The pre-commit hooks (run `pre-commit install` once per clone if not yet
   installed) will fire ruff, mypy, end-of-file-fixer, and the
   snapshot-regression pytest hook. If `end-of-file-fixer` or any other
   auto-fixer modifies a file, re-stage and retry — do **not** bypass with
   `--no-verify`. After the commit, run `git status` to confirm the working
   tree is clean (modulo the intentionally-untracked files), then report
   (format below). Never push.

## Debugging playbook (symptom → likely cause → fix)

| Symptom | Likely cause | First MCP probe | Fix |
|---|---|---|---|
| **0 jobs**, anchors exist on page | Wrong path prefix, or job links are on a **different origin** than `job_board_url` | `browser_evaluate` the host-histogram recipe; if a non-company host dominates, the board is cross-origin | Fix `sample_job_url` (to correct the prefix) and/or `job_board_url` (to the real board host). |
| **0 jobs**, page shows listings | Links are **query-string style** but prefix derived too deep, OR jobs aren't `<a>` tags | `browser_snapshot` — if listing rows are `button`/`generic` with no link, they aren't `<a>`; otherwise `browser_evaluate` to inspect pathnames and search strings | If query-style, pick a `sample_job_url` whose path equals the listing prefix. If they're buttons/divs, report as a structural limitation. |
| **0 jobs**, MCP shows few/no anchors | Jobs are **JS-rendered or lazy-loaded** and hadn't loaded | `browser_wait_for` then `browser_evaluate` to scroll; check `browser_network_requests` for the XHR fetching the listings | If an XHR returns the job JSON, point `job_board_url` at the host serving that XHR. Otherwise re-run the script with higher `--wait` and `--scroll`. |
| **Fewer than expected** | Pagination, lazy-load needing more scroll, or a location filter narrowed results | `browser_snapshot` for a "next page" / "load more" control; `browser_click` it and re-run the count recipe | Re-run the script with more `--scroll`. If a location filter is being applied by the browser-use agent and shrinking the set, note it (the lab's `GOAL_PROMPT` filtering is optional). |
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
in the extraction (`extraction/dom/`) or navigation (`navigation/`) modules
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
see the Deel entry in `blockers/INTEGRATION_BLOCKERS_R2.md`).

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
  integration in a single `feat:` commit covering only the `catalog/companies.py`
  edit and the new `tests/fixtures/snapshots/<slug>/` directory, subject line
  only, no body. Do not stage or commit anything else (especially not
  `new-companies.json` or `ARCHITECTURE.md` — both are intentionally
  untracked). On a blocker, do **not** commit at all; leave changes in the
  working tree for human review. Never `git push`, never create branches,
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
  into any production module (`extraction/`, `navigation/`, `catalog/`, or the
  `agent.py` / `config.py` shims). Do not declare the task Done based on MCP
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
  Mypy; imports as `from job_agent_lab.X`.

## Definition of Done

All of the following hold:

1. The company is appended to `COMPANIES` in `catalog/companies.py` with all
   five fields (`name`, `aliases`, `job_board_url`, `sample_job_url`,
   `expected_jobs`).
2. The bundled `extractor_ground_truth.py` script returns **exactly
   `expected_jobs`**.
3. A real `uv run job-agent-lab -c <handle>` run reports
   `metadata.verdict == "match"` (SYS-9's machine-authored check
   inside `build_report`) with `agent_completed: true`. A `match`
   line annotated with `(agent reported step errors)` — that is,
   the count is right *and* `agent_had_errors: true` — is landable
   but requires a look at the run log first to rule out a
   non-transient issue.
4. A regression snapshot exists at `tests/fixtures/snapshots/<slug>/`
   (`page.html` + `metadata.json`), and
   `uv run --group test pytest tests/` passes against the whole corpus.
5. If any code was changed: `ruff format`, `ruff check`, and
   `mypy src scripts tests` all pass, and the change is generic (no
   site-specific hacks).
6. The integrated company's entry has been removed from the untracked
   `new-companies.json` queue file at the repo root.
7. A single `feat: add <Name> to company test sample` commit has been
   created, staging only `catalog/companies.py` and the new
   `tests/fixtures/snapshots/<slug>/` directory, subject line only with no
   body or trailers; all pre-commit hooks passed (ruff, mypy,
   end-of-file-fixer, snapshot regression). Nothing was pushed.
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
   expected jobs:   <N>
   extracted jobs:  <N>   (script: <N>, full run: <N>)
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
