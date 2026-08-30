"""Task prompt for the browser-use agent.

``GOAL_PROMPT`` is the shared instruction template appended to the
per-company task text. Its region-specific *tokens* — the intro noun
phrase, the quoted filter-option list, and the preferred/fallback
preference pair — are rendered from a :class:`TargetRegion` via
:func:`build_goal_prompt` (SYS-4), and ``GOAL_PROMPT`` itself is the
:data:`COSTA_RICA_LATAM` render.

SYS-6 decomposes the previously monolithic f-string into six named
clause constants (``_INTRO_CLAUSE``, ``_STEP_1A_IDENTIFY_CLAUSE``,
``_STEP_1B_INSPECT_CLAUSE``, ``_STEP_1C_DECIDE_CLAUSE``,
``_STEP_2_EXTRACT_CLAUSE``, ``_IMPORTANT_BULLETS_CLAUSE``) that
:func:`build_goal_prompt` concatenates in order and then interpolates
via :meth:`str.format` — the "assembled from clauses" shape called out
in ``ARCHITECTURE_PROPOSAL.md`` §4.5.

SYS-6 Task 4 ships two capability additions on top of the decomposed
skeleton (plan decision 2):

* **C3 — native ``<select>`` vocabulary + submit-click.** ``1a`` gains
  a sentence teaching the agent that a native ``<select>`` element is
  also a filter (identified by ``name`` / ``id`` / associated label
  tokens), and Case B gains two sentences covering ISO country codes
  (``CR`` / ``CRI``) and the "click the Search / Apply / Submit /
  Filter button after selecting" rule for form-wrapped filters. Case C
  is deliberately untouched — the ISO wording lives on the Case B
  branch only.

* **C4 — collapsed-section reveal.** The STEP 2 preamble gains a
  sentence instructing the agent to click role-count headers
  (``N open roles`` / ``N positions`` / ``N jobs``) or explicit
  Expand/Show affordances before extracting, with an anti-scope guard
  against unrelated accordions (FAQ, cookie banner, footer, help).
  A matching IMPORTANT bullet reminds the agent that scrolling alone
  does not expand these sections.

Neither addition touches ``TargetRegion`` — the ISO handling is
clause-level wording only, not a change to the region's
``filter_tokens`` list. Golden tests in
``tests/unit/test_prompt_render.py`` pin the assembled render.

SYS-16 appends two sentences to ``1a`` (C3.1), both region-agnostic —
the first reuses the ``{quoted}`` placeholder rather than naming any
region:

* **Option-text discovery.** A control whose ``name``, ``id``, and
  label mention none of the C3 tokens is still the location filter
  when its *option texts* include the region tokens. This closes the
  C3 residual recorded in
  ``blockers/INTEGRATION_BLOCKERS_R2.md``: the C3 sentence above keys
  on attributes, so an attribute-anonymous control falls through to
  Case A and the whole board over-extracts.

* **Visible-control anchoring.** The second sentence is *evidence-
  forced* rather than proposed, and it is what keeps the first
  sentence safe. Dev.Pro's two anonymous ``<select>`` elements are
  ``display: none`` decoys behind a W3Schools-style custom widget
  (``div.custom-select`` wrapping the hidden ``<select>`` plus a
  visible ``div.select-selected`` trigger and a ``div.select-items``
  option list). Option texts live on the *hidden* select, so a
  discovery rule that stops at "read the option texts, then use that
  select" points the agent at an element it cannot click — a
  regression risk on every board using this very common pattern
  (W3Schools custom-select, Select2, Chosen). Anchoring interaction
  to the visible widget is what makes option-text discovery
  actionable.

Measurement note (SYS-16): the C3.1 clause is a *non-regression*
change on the corpus as it stands, not a demonstrated capability win.
No board currently in the corpus or the queue exposes a **visible**
attribute-anonymous control that the pre-SYS-16 prompt misses —
Dev.Pro, the motivating board, is discovered correctly by the existing
combobox sentence (3/3 pre-clause runs opened its ``Location``
widget). See ``spike/SYS_16_RESULTS.md`` for the full measurement
table.

Each clause is authored as a triple-quoted string with real newlines
in the source (rather than escape sequences), so file round-trips
cannot silently drop or merge line breaks. Ruff's line-length rule
stays disabled for this file via ``[tool.ruff.lint.per-file-ignores]``
in ``pyproject.toml`` because the prompt is a data payload, not
human-authored code. The clauses carry :meth:`str.format` placeholders
(``{intro_label}``, ``{quoted}``, ``{preference}``) rather than
f-string interpolations so their region-agnostic bodies are
inspectable at module level without constructing a :class:`TargetRegion`.
"""

from __future__ import annotations

from job_agent_lab.domain.region import COSTA_RICA_LATAM, TargetRegion

# ---------------------------------------------------------------------------
# Prompt clauses (SYS-6 §4.5).
#
# Each clause carries its own trailing newline(s) so concatenation
# reproduces the pre-SYS-6 monolithic triple-quoted f-string shape. The
# ``{intro_label}``, ``{quoted}``, and ``{preference}`` placeholders are
# resolved once by :func:`build_goal_prompt` via :meth:`str.format`.
#
# C3 (native ``<select>``) additions live inside
# ``_STEP_1A_IDENTIFY_CLAUSE`` (identify-vocab) and
# ``_STEP_1C_DECIDE_CLAUSE`` (Case B: ISO codes + submit-click). C4
# (accordion reveal) additions live inside ``_STEP_2_EXTRACT_CLAUSE``
# (preamble sentence) and ``_IMPORTANT_BULLETS_CLAUSE`` (new bullet).
# ---------------------------------------------------------------------------

_INTRO_CLAUSE: str = """You are on a company career page with a job board listing open positions. Your task is to return the URLs for job listings that apply to the target region ({intro_label}).

"""

_FILTER_ALREADY_APPLIED_CLAUSE: str = """NOTE: the location filter is already applied through the page URL; do NOT interact with any location filter control; treat the page as Case B already completed.

"""

_STEP_1A_IDENTIFY_CLAUSE: str = """STEP 1 — FILTER (always start here):

1a. IDENTIFY. Scan the page for any location or region filter: a dropdown, combobox, checkbox group, tag list, or radio group whose label mentions location, region, country, city, office, or similar. A CLOSED combobox or dropdown still counts as a filter — do not skip it. A native HTML select element also counts as a filter — identify it by a name, id, or associated label containing tokens like country, location, region, office, city, pais, or país. A control whose name, id, and label mention none of those tokens is still the location filter if the options it offers include {quoted} — identify such a control by its option texts. Always interact with the control the page actually shows you: where a native select element is hidden behind a custom dropdown widget, use that widget's visible trigger and option list, not the hidden select.

"""

_STEP_1B_INSPECT_CLAUSE: str = """1b. INSPECT. If a candidate filter exists, OPEN it (click the dropdown, expand the combobox, reveal the tag list) so its full option list is visible. You cannot decide between Case B and Case C without seeing the actual options.

"""

_STEP_1C_DECIDE_CLAUSE: str = """1c. DECIDE. Handle exactly one of these three cases.

Case A — no location/region filter of any kind exists on the page. Go straight to STEP 2 and extract every listing.

Case B — the filter's options include {quoted}. Select the first matching option, preferring {preference} when both are offered. Filter options may be ISO country codes rather than full country names — treat "CR" or "CRI" as Costa Rica. If the filter sits in a form with a Search, Apply, Submit, or Filter button, click that button after selecting the option so the filter is applied. Then go to STEP 2 and extract the filtered listings.

Case C — the filter's options are visible, and none of {quoted} appear among them. Do NOT extract. Call the report_no_matching_location_filter tool. This finishes the run with an empty result, meaning the company has no listings applicable to the target region.

"""

_STEP_2_EXTRACT_CLAUSE: str = """STEP 2 — EXTRACT (Cases A and B only):
Before extracting, if listings are grouped under collapsed section headers — for example headers labelled with a role count like "3 open roles", "5 positions", or "10 jobs", or an explicit Expand or Show affordance — click each such header first to reveal its listings. Do not expand unrelated accordions (FAQ, cookie banner, footer, help). Once the page is ready (filter applied if applicable, collapsed sections revealed, content loaded, scrolled to reveal all listings), call the extract_job_links tool. It takes no arguments — the tool already knows how to identify valid job link URLs based on the company configuration.

"""

_IMPORTANT_BULLETS_CLAUSE: str = """IMPORTANT:
- Do NOT click into individual job pages. Stay on the listing page.
- If you need to scroll to reveal more listings, do so before extracting.
- Use the extract_job_links tool to extract — do NOT use find_elements.
- In Case C, call report_no_matching_location_filter INSTEAD of extract_job_links. Do not fall back to extract_job_links in Case C.
- A closed dropdown/combobox labelled with a location-like term (Office, Location, Region, Country, City, ...) is a filter. Open it before deciding between Cases B and C.
- Scrolling alone does not expand collapsed sections — click each section header to reveal the listings it contains.
"""

# Assembled in the order the agent reads them; keep this tuple as the
# single source of truth so a future clause can be inserted with a
# one-line edit rather than a body rewrite.
_CLAUSES: tuple[str, ...] = (
    _INTRO_CLAUSE,
    _STEP_1A_IDENTIFY_CLAUSE,
    _STEP_1B_INSPECT_CLAUSE,
    _STEP_1C_DECIDE_CLAUSE,
    _STEP_2_EXTRACT_CLAUSE,
    _IMPORTANT_BULLETS_CLAUSE,
)


def build_goal_prompt(
    region: TargetRegion,
    *,
    filter_already_applied: bool = False,
) -> str:
    """Render ``GOAL_PROMPT`` for the given :class:`TargetRegion`.

    Concatenates the six clause constants (SYS-6 §4.5) and resolves
    their region-specific placeholders — ``{intro_label}``, ``{quoted}``,
    and ``{preference}`` — in one :meth:`str.format` pass. The
    surrounding STEP 1 / STEP 2 sequence and the Case A / Case B / Case C
    decision tree are hard-coded in the clause bodies; the goldens in
    ``tests/unit/test_prompt_render.py`` pin the assembled render.

    ``filter_already_applied`` (SYS-12 :attr:`RuntimeHooks.filter_already_applied`)
    is a conditional-clause switch. When ``False`` (the default) the
    render is byte-identical to the pre-SYS-12 shape — every existing
    golden stays untouched. When ``True`` the
    ``_FILTER_ALREADY_APPLIED_CLAUSE`` sentence is inserted between
    the intro and STEP 1, telling the agent that the page URL already
    encodes the location filter and no filter-control interaction is
    needed. Used by boards whose ``job_board_url`` carries the region
    in its query string (Progress: ``?location=Costa+Rica``) where an
    unconditional Case B click destroys the pre-filter.
    """
    clauses: list[str] = list(_CLAUSES)
    if filter_already_applied:
        # Insert immediately after the intro so the agent reads the
        # "filter already applied" instruction before it starts STEP 1's
        # filter-identification pass. Index 1 = between _INTRO_CLAUSE
        # and _STEP_1A_IDENTIFY_CLAUSE.
        clauses.insert(1, _FILTER_ALREADY_APPLIED_CLAUSE)
    return "".join(clauses).format(
        intro_label=region.intro_label,
        quoted=region.format_quoted_options(),
        preference=region.format_preference_pair(),
    )


GOAL_PROMPT: str = build_goal_prompt(COSTA_RICA_LATAM)
