# Issue: postings lost or corrupted before the eligibility model sees them

Status: open, not started. Recorded 2026-11-21.

## Summary

In a sample of 164 postings from 50 catalog companies, the pipeline lost or
corrupted far more postings while fetching them than the eligibility model
got wrong:

| Outcome | Postings | Share |
|---|---|---|
| Not fetched at all | 19 | 12% |
| Fetched, but not a real posting (block page or closed job) | 23 | 14% |
| Fetched with degraded text (raw HTML, no line breaks, missing location) | at least 34 | overlaps the row above |
| For comparison: wrong eligibility decisions on the blind set | | about 3% of answers |

For the first two rows the whole company is usually affected, not one posting.
For example, every West Monroe, VML, McKinsey, UST, and Deloitte posting in the
sample was lost.

## How the sample was taken

On 2026-11-21:

1. A model-free `job-discovery scan` of the 54 catalog boards that need no agent
   ran into a scratch database, so the real one was untouched. It stored 709 URLs
   from 50 companies.
2. Up to 4 URLs per company were sampled at random, 164 in total. The 8 companies
   in the eligibility test corpus were excluded.
3. Each posting was fetched with `scripts/capture_posting.py`, which calls the
   pipeline's own `acquire()` ladder. Failures were re-checked with
   `scripts/probe_posting.py`.

18 capture failures were artifacts of the capture tool, not pipeline losses, and
are excluded above. The tool's secret scanner refused to write the fixture, and
the pipeline fetches those postings fine (Veeam, Moody's, Heraeus, Midland,
Oowlish, Cornelis Networks, Oliver). Earlier I reported this category as "about a
third lost". Counted correctly, it is about a quarter.

## 1. Not fetched (19 postings, terminal or retried until `failed`)

| Company | Lost | Failure | Example URL | Notes |
|---|---|---|---|---|
| West Monroe | 4/4 | `off_site_redirect` (terminal) | https://westmonroe.com/careers/job-details-experienced-professionals?gh_jid=6181130004 | Greenhouse board; the posting lives on the company site |
| Elastic | 3/3 | `off_site_redirect` (terminal) | https://jobs.elastic.co/jobs?gh_jid=8281841 | Same pattern |
| VML | 4/4 | `blocked` (HTTP 403) | https://www.vml.com/careers/job/8539856002?gh_jid=8539856002 | Same pattern; the site refuses automated clients |
| McKinsey | 4/4 | `timeout` | https://www.mckinsey.com/careers/search-jobs/jobs/associateintern-14918 | Plain HTTP fails at once; known blocker C23 |
| UST | 4/4 | `not_found` (HTTP 404; 403 to curl) | https://www.ust.com/jobs/10000006286 | Even the catalog's own `sample_job_url` is refused |

**Likely causes:**
- **West Monroe, Elastic and VML** are Greenhouse boards whose job URLs point to
  the company's own site (`?gh_jid=`), which embeds the Greenhouse posting. The
  fetch either leaves the allowed hosts (`off_site_redirect`) or is refused
  (403). The off-site case matches follow-up **O4** in `CLAUDE.md` (third-party
  iframe navigation rejects the whole render). That mechanism has not been
  confirmed.
- **McKinsey and UST** refuse automated clients.

## 2. Fetched, but not a real posting (23 postings)

These pages pass every fetch check and reach the model as if they were open
postings.

| Company | Count | What the page says | Example URL |
|---|---|---|---|
| Deloitte | 4/4 | "ACCESO O FUNCIONALIDAD BLOQUEADA" (a block page served with HTTP 200, 580 characters) | https://carrera.deloitteslatam.com/job/Costa-Rica-QA-Lead-architect-Costa-Rica-%2C-Guatemala%2C-Nicaragua%2C-Honduras%2C-El-Salvador-San-Jos%C3%A9/1431559933/ |
| HPE | 4/4 | "the job you are trying to apply for has been filled" | https://careers.hpe.com/us/en/job/1209590/Supply-Chain-Supply-Demand-Planning-BSA-Project-Manager |
| Zimmer Biomet | 4/4 | "…has been filled" | https://careers.zimmerbiomet.com/us/en/job/11629/IT-MOM-Sr-Technician |
| TD SYNNEX | 3/4 | "…has been filled" | https://careers.tdsynnex.com/us/en/job/R57326/Apple-Mac-OS-Sales-Engineer |
| Roche | 4/4 | "the job you are trying to apply for is not available anymore" | https://careers.roche.com/global/en/job/202609-123432/Data-Science-Engagement-Lead |
| Philips | 4/4 | "Sorry! We couldn't find any jobs that match your search" | https://www.careers.philips.com/global/en/job/593889/Director-Quality-Programs-Integrated-Supply-Chain-ISC |

**Likely causes:**
- **The closure list misses these phrases.** `CLOSED_PHRASES` in
  `src/job_pipeline/content/text.py` has "this position has been filled" but not
  Phenom's "the job you are trying to apply for has been filled" or "…is not
  available anymore". These pages also carry the old JSON-LD description, so the
  ladder picks it up as content.
- **Discovery lists jobs the posting page says are closed.** All five closed-page
  companies are Phenom boards. The scan ran on the same day, so the Phenom search
  API appears to return jobs whose page shows them as filled. This is unverified.
  It could also be a locale or path variant, since several URLs use `/us/en/` for
  Costa Rica roles.
- **Deloitte block pages pass as content.** Deloitte answers the pipeline's
  fetcher with a block page and status 200. Nothing classifies it as `blocked`.

## 3. Fetched with degraded text

- **Raw HTML in the text (28 postings, every Phenom board).** BCG, Cisco, HPE,
  Philips, Roche, TD SYNNEX and Zimmer Biomet store the JSON-LD description with
  its HTML tags (`<p>`, `<li>`, `style=…`) still in it.
- **No line breaks (6 postings).** Medtronic and LSEG Workday postings come out
  as one long line.
- **Location missing from the text.** Edwards shows a `Location` label with no
  value. Medtronic's text has no location at all, although its URL contains
  `Alajuela-Coyol-Alajuela-Costa-Rica`. The model can only answer "states nothing
  about location" (unknown) for these.
- **A listing page stored as a posting.** Reap's discovered URL is the filtered
  listing (`https://careers.reap.global/jobs?location=Latin+America`). The stored
  text is an 855-character company blurb.

## Suggested fixes, in order of value

1. **Greenhouse boards: take the posting from the Greenhouse API.** The public
   endpoint `boards-api.greenhouse.io/v1/boards/<token>/jobs/<id>` returns the
   description and location without visiting the company site. That avoids the
   redirect and the 403, and fixes West Monroe, Elastic and VML at once.
2. **Closed and blocked pages:**
   - add the Phenom phrases ("the job you are trying to apply for has been filled",
     "…is not available anymore") to `CLOSED_PHRASES`;
   - treat known block pages (Deloitte's text) as `blocked`;
   - find out why the Phenom discovery lists these jobs at all.
3. **Strip HTML from JSON-LD descriptions** before storing them. This affects every
   Phenom board.
4. **Keep the location** when the page shows it outside the description (Edwards,
   Workday). Workday's JSON-LD or the URL path may carry it.
5. **Reap:** fix discovery so the listing URL is not stored as a posting.
6. **McKinsey and UST:** decide per company whether to support them (browser
   settings, a different fetch path) or drop them from the catalog.

## How to verify a fix

- Re-run a single posting: `uv run python scripts/probe_posting.py -c "<Company>" <url>`.
  It prints the method or failure category, the status and the text length, and
  never the posting text.
- Re-take the sample with the same method, and compare the counts in the table
  at the top.
- The 2026-11-21 sample captures were kept only in a temporary scratch
  directory, so they are not in the repository.
