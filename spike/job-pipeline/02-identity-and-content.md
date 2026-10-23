# Phase 2 — Identity and content acquisition

Goal: given a discovered URL, produce a stable identity key and the clean
posting text the leaves will read, with every failure mode named. Also the
capture script that builds the golden corpus the next two phases need.
One pull request for the code, a second for the fixtures.

Nothing in this phase calls a model or writes to the database. Every module
is a pure function or an I/O adapter behind a small dataclass, which is what
lets Phase 5 compose them without tests that need the network.

## Task 2.1 — URL identity

- Objective: `url_key(url) -> str` and the denylist, pinned against the
  real snapshot.
- Files: `src/job_pipeline/identity.py`,
  `tests/fixtures/identity/snapshot_urls.tsv`,
  `tests/unit/test_pipeline_identity.py`.
- Changes:
  - [ ] `NON_IDENTIFYING_PARAMS` frozenset: `title`, `name`, `jobName`,
        `lastSelectedFacet`, `selectedLocationsFacet`, `in_iframe`,
        `locationCountry`, `locationId`, `source`, `new`, `trid`,
        `locations`, `mode`, `l`, `lang`, `c`, `export-pdf`; plus the
        prefix rule `utm_`. Each name carries a one-line comment naming the
        board it was observed on.
  - [ ] `url_key`: lowercase scheme and host, drop default ports, drop the
        fragment, strip one trailing slash from the path (never the root),
        filter the query, sort remaining pairs, re-encode with
        `urlencode(..., doseq=True)`. Percent-encoding is normalized by
        decoding then re-encoding so `%20` and `+` agree.
  - [ ] `fingerprint(title, text) -> str`: sha256 over casefolded,
        whitespace-collapsed title plus the first `FINGERPRINT_CHARS` of the
        same normalization of text.
  - [ ] Export the 2026-10-23 snapshot once with
        `sqlite3 -separator $'\t' data/job-discovery.db "select company_slug, url from job_urls"`
        into the fixture (1,504 rows). The test asserts: 1,503 distinct
        `(slug, key)` pairs; the only merge is the two Excel Nearshore URLs;
        no two different Accenture `id` values share a key; keys are stable
        under parameter reordering and fragment addition.
- Dependencies: Phase 1.
- Verification: the unit test; `python -I` one-liner over the fixture prints
  the same counts the design conversation measured.
- Effort: 3 hours.

## Task 2.2 — Acquired content model and outcome taxonomy

- Objective: one frozen dataclass every rung of the ladder returns, and the
  closed set of acquisition outcomes the lifecycle maps to statuses.
- Files: `src/job_pipeline/content/__init__.py`,
  `src/job_pipeline/content/model.py`.
- Changes:
  - [ ] `AcquiredContent(text, title, resolved_url, http_status, method,
        requisition_id, jsonld, retrieved_at, content_hash)` with `method`
        in `("jsonld", "main_content", "rendered_jsonld", "rendered_main")`.
  - [ ] `AcquisitionFailure(category, message, http_status, resolved_url)`
        with `category` in `("not_found", "closed", "blocked", "timeout",
        "transport", "server_error", "empty_content", "off_site_redirect",
        "malformed")`.
  - [ ] `RETRYABLE_CATEGORIES` and `TERMINAL_CATEGORIES` as frozensets whose
        union is asserted equal to the full set by a test. `not_found`,
        `closed`, `malformed`, `off_site_redirect` are terminal; the rest
        retry.
  - [ ] `content_hash` is sha256 of the normalized text, so the same
        posting fetched twice stores one content row.
- Dependencies: Phase 1.
- Verification: `tests/unit/test_pipeline_content_model.py` covers the
  partition assertion and hash stability.
- Effort: 1.5 hours.

## Task 2.3 — HTTP fetch

- Objective: one httpx call with the behaviours a careers site needs.
- Files: `src/job_pipeline/content/fetch.py`,
  `tests/unit/test_pipeline_fetch.py` (respx).
- Changes:
  - [ ] Async `fetch(url, *, client) -> FetchResult(html, status, final_url,
        headers)`; follow redirects up to 10; timeout from settings; UA from
        `job_discovery.settings.derive_plausible_ua` applied to a fixed
        modern Chrome UA string (no browser launch on the HTTP path).
  - [ ] Map status to failure category: 404 and 410 `not_found`; 401, 403,
        429, and 503 with a challenge body `blocked`; other 5xx
        `server_error`; `httpx.TimeoutException` `timeout`; other
        `httpx.TransportError` `transport`.
  - [ ] A redirect whose final host is neither the original host nor a
        known ATS host (`greenhouse.io`, `myworkdayjobs.com`, `icims.com`,
        `lever.co`, `ashbyhq.com`, `smartrecruiters.com`, `oraclecloud.com`,
        `successfactors.com`, `csod.com`, `ultipro.com`, `bamboohr.com`,
        `phenompeople.com`, list kept in the module) is
        `off_site_redirect`; a redirect to the company's listing root is
        `closed`.
  - [ ] Tests: each mapping; redirect chain records `final_url`; the
        listing-root redirect uses the company's `job_board_url` passed in.
- Dependencies: 2.2.
- Verification: unit tests; `scripts/probe_posting.py` from 2.7 against two
  live boards.
- Effort: 3 hours.

## Task 2.4 — JSON-LD JobPosting extraction

- Objective: when a page ships schema.org data, take it before touching the
  rendered body.
- Files: `src/job_pipeline/content/jsonld.py`,
  `tests/fixtures/postings/` (from 2.8), `tests/unit/test_pipeline_jsonld.py`.
- Changes:
  - [ ] Parse every `<script type="application/ld+json">` with the stdlib
        `html.parser`, tolerate arrays and `@graph`, select objects whose
        `@type` is or contains `JobPosting`.
  - [ ] `JobPostingLd(title, description_text, identifier, date_posted,
        valid_through, locations, employment_type, hiring_org, raw)`.
        `description` is HTML in practice: convert to text with trafilatura's
        `html2txt` or an equivalent so headings and list items survive as
        lines.
  - [ ] `validThrough` in the past marks the posting `closed`.
  - [ ] Malformed JSON in one block does not discard the others.
  - [ ] Tests on four recorded pages with JSON-LD (choose a Workday, a
        Greenhouse, an iCIMS, and a Phenom page from the corpus) and two
        without.
- Dependencies: 2.2.
- Verification: unit tests.
- Effort: 3 hours.

## Task 2.5 — Main-content extraction

- Objective: clean text from pages without structured data, without
  per-company selectors.
- Files: `src/job_pipeline/content/text.py`,
  `tests/unit/test_pipeline_text.py`.
- Changes:
  - [ ] `main_text(html, url) -> tuple[str, str | None]` returns text and
        the page title via trafilatura with `include_tables=True`,
        `include_links=False`, `favor_recall=True`, output as plain text with
        list markers preserved.
  - [ ] `normalize(text) -> str`: NFKC, collapse runs of spaces, collapse
        more than two newlines, strip. This is the same function the
        evidence grounding check in Phase 3 uses, exported from here.
  - [ ] `looks_closed(text) -> bool` over a short phrase list: "no longer
        accepting applications", "this job is no longer available", "this
        position has been filled", "the job you are looking for is no
        longer open", plus Spanish equivalents. Phrase list is a module
        constant with the board each phrase was seen on.
  - [ ] Texts under `MIN_CONTENT_CHARS` return the `empty_content` signal
        to the ladder.
  - [ ] Tests on the recorded corpus: every fixture page yields text that
        contains its known title; a captured closed page returns
        `looks_closed`.
- Dependencies: 2.2.
- Verification: unit tests.
- Effort: 3 hours.

## Task 2.6 — Rendered fallback

- Objective: client-rendered pages get one headless render, no agent.
- Files: `src/job_pipeline/content/render.py`,
  `tests/snapshots/test_pipeline_render.py`.
- Changes:
  - [ ] `render(url) -> str` launches through
        `job_discovery.settings.launch_capture_browser`, navigates with
        `navigation_timeout_ms(RENDER_WAIT_SEC)`, waits `RENDER_WAIT_SEC`,
        scrolls `RENDER_SCROLL_COUNT` times, returns `page.content()`.
        Reusing the discovery constants keeps one definition of "settled".
  - [ ] One browser per pipeline run, passed in; the function takes a
        `Browser` and opens a fresh context per page so a hung page cannot
        poison the next.
  - [ ] Test lives in `tests/snapshots/` because it needs Chromium: serve a
        page whose body is filled by a script after load, assert the
        rendered text contains it and the plain fetch does not.
- Dependencies: 2.2.
- Verification: snapshot test; CI snapshots job.
- Effort: 3 hours.

## Task 2.7 — The ladder

- Objective: one entry point that tries the rungs in order and returns
  either content or a classified failure.
- Files: `src/job_pipeline/content/ladder.py`,
  `scripts/probe_posting.py`, `tests/unit/test_pipeline_ladder.py`.
- Changes:
  - [ ] `acquire(url, *, company, http, browser) -> AcquiredContent |
        AcquisitionFailure`:
        1. fetch; a failure short-circuits.
        2. JSON-LD present with description over the minimum: method
           `jsonld`; the text is the JSON-LD description followed by the
           main text, deduplicated by paragraph, so a posting whose JSON-LD
           carries only a summary still gets its body.
        3. else main text over the minimum: `main_content`.
        4. else, if a browser is available, render once and repeat 2 and 3
           with the `rendered_` methods.
        5. else `empty_content`.
  - [ ] `looks_closed` is checked on whichever text wins and overrides to
        `closed`.
  - [ ] `scripts/probe_posting.py URL` prints method, status, title,
        requisition id, text length, the first 40 lines, and the
        `url_key`. This is the operator's first tool when a board extracts
        badly, the counterpart of `probe_board.py`.
  - [ ] Tests with respx and a fake renderer: each rung chosen for the
        right reason; the closed override; the browser not called when the
        plain fetch suffices.
- Dependencies: 2.3 to 2.6.
- Verification: unit tests; probe ten live URLs across ATS types and record
  which rung each used in the PR description.
- Effort: 4 hours.

## Task 2.8 — Golden corpus capture

- Objective: thirty frozen postings with text, metadata, and nothing else
  yet, the raw material for the expectations written in Phases 3 and 4.
- Files: `scripts/capture_posting.py`,
  `tests/fixtures/postings/<slug>__<nn>/{page.html, content.txt, meta.json}`,
  `tests/fixtures/postings/MANIFEST.md`.
- Changes:
  - [ ] `capture_posting.py -c HANDLE URL` runs the ladder, writes the raw
        HTML, the normalized text, and `meta.json` with `url`, `url_key`,
        `resolved_url`, `method`, `http_status`, `requisition_id`,
        `captured_at`, `content_hash`. A `--rendered` flag forces the browser
        rung for a page that needs it.
  - [ ] Selection, recorded in `MANIFEST.md` with the reason for each:
        - 6 API-strategy boards (2 Greenhouse, 2 Phenom, 1 Talentbrew,
          1 BambooHR) so the pure-text path is exercised on structured sources.
        - 12 DOM boards spanning Workday, iCIMS, Oracle, SuccessFactors,
          Cornerstone, UKG, Ashby, Lever, and three custom sites.
        - 4 Spanish-language postings.
        - 3 postings expected ineligible (explicit other-country restriction,
          exhaustive list without Costa Rica, onsite elsewhere).
        - 2 postings expected unknown (no location; bare "Remote").
        - 3 sparse postings (no benefits, few responsibilities, duties mixed
          with requirements).
  - [ ] Secrets scan over the captured HTML before commit: no cookies, no
        tokens in URLs; strip `Set-Cookie` headers and any `token=` query
        value from `meta.json`.
  - [ ] Fixture size budget 5 MB total to stay under the pre-commit
        large-file check.
- Dependencies: 2.7.
- Verification: `tests/unit/test_pipeline_corpus.py` asserts every fixture
  directory has the three files, every `content.txt` is over the minimum,
  and `MANIFEST.md` lists every directory.
- Effort: 5 hours, including selection.

## Testing strategy

| Test type | Scope | Priority |
|---|---|---|
| Unit | identity against the 1,504-URL fixture; failure taxonomy partition | P0 |
| Unit | fetch mapping with respx; JSON-LD on six pages; text on the corpus; ladder rung choice | P0 |
| Snapshot | rendered fallback in Chromium | P1 |
| Manual | `probe_posting.py` on ten live URLs, one per ATS family | P1 |

## Done

- [ ] PR 1 `feat: add url identity and the posting content ladder`.
- [ ] PR 2 `test: capture the thirty-posting golden corpus`.
- [ ] Every fixture page attributed to a rung in `MANIFEST.md`; no fixture
      required a per-company override.
