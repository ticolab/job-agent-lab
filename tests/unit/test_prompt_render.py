"""Golden byte-identity tests for the region-parametrised prompt renders.

SYS-4 Task 1 extracts the region-specific tokens (intro noun phrase,
quoted / unquoted option list, preference pair) from ``GOAL_PROMPT``
and the ``report_no_matching_location_filter`` tool description into
:class:`job_agent_lab.domain.region.TargetRegion`. That refactor was
behaviourally invisible to the agent — the model received the exact
same bytes it did in the pre-SYS-4 literals — and the goldens below
were the frozen pre-SYS-4 strings copied verbatim.

SYS-6 Task 4 lands the first deliberate clause-content change on top
of that baseline: two new C3 sentences (native ``<select>`` vocab in
1a, ISO country codes + Search/Apply/Submit click in Case B) and one
new C4 block (collapsed-section reveal preamble in STEP 2 plus a
matching IMPORTANT bullet). The ``GOLDEN_GOAL_PROMPT`` below is
updated in lockstep with ``src/job_agent_lab/navigation/prompt.py``
so a byte-drift can never sneak in undocumented — any future clause
change must land here in the same commit.

The Case C tool description and the surrounding STEP 1 / STEP 2 /
IMPORTANT skeleton remain unchanged. Triple-quoted string literals
with real newlines in source keep the fixtures immune to
escape-round-trip errors when they are next edited.
"""

from __future__ import annotations

from job_agent_lab.domain.region import COSTA_RICA_LATAM
from job_agent_lab.navigation.controller import build_no_match_description
from job_agent_lab.navigation.prompt import GOAL_PROMPT, build_goal_prompt

# ---------------------------------------------------------------------------
# Golden: GOAL_PROMPT for COSTA_RICA_LATAM (SYS-6 Task 4 render).
# ---------------------------------------------------------------------------
GOLDEN_GOAL_PROMPT: str = """You are on a company career page with a job board listing open positions. Your task is to return the URLs for job listings that apply to the target region (Costa Rica or Latin America).

STEP 1 — FILTER (always start here):

1a. IDENTIFY. Scan the page for any location or region filter: a dropdown, combobox, checkbox group, tag list, or radio group whose label mentions location, region, country, city, office, or similar. A CLOSED combobox or dropdown still counts as a filter — do not skip it. A native HTML select element also counts as a filter — identify it by a name, id, or associated label containing tokens like country, location, region, office, city, pais, or país. A control whose name, id, and label mention none of those tokens is still the location filter if the options it offers include "Costa Rica", "CR", "LATAM", or "Latin America" — identify such a control by its option texts. Always interact with the control the page actually shows you: where a native select element is hidden behind a custom dropdown widget, use that widget's visible trigger and option list, not the hidden select.

1b. INSPECT. If a candidate filter exists, OPEN it (click the dropdown, expand the combobox, reveal the tag list) so its full option list is visible. You cannot decide between Case B and Case C without seeing the actual options.

1c. DECIDE. Handle exactly one of these three cases.

Case A — no location/region filter of any kind exists on the page. Go straight to STEP 2 and extract every listing.

Case B — the filter's options include "Costa Rica", "CR", "LATAM", or "Latin America". Select the first matching option, preferring "Costa Rica"/"CR" over "LATAM"/"Latin America" when both are offered. Filter options may be ISO country codes rather than full country names — treat "CR" or "CRI" as Costa Rica. If the filter sits in a form with a Search, Apply, Submit, or Filter button, click that button after selecting the option so the filter is applied. Then go to STEP 2 and extract the filtered listings.

Case C — the filter's options are visible, and none of "Costa Rica", "CR", "LATAM", or "Latin America" appear among them. Do NOT extract. Call the report_no_matching_location_filter tool. This finishes the run with an empty result, meaning the company has no listings applicable to the target region.

STEP 2 — EXTRACT (Cases A and B only):
Before extracting, if listings are grouped under collapsed section headers — for example headers labelled with a role count like "3 open roles", "5 positions", or "10 jobs", or an explicit Expand or Show affordance — click each such header first to reveal its listings. Do not expand unrelated accordions (FAQ, cookie banner, footer, help). Once the page is ready (filter applied if applicable, collapsed sections revealed, content loaded, scrolled to reveal all listings), call the extract_job_links tool. It takes no arguments — the tool already knows how to identify valid job link URLs based on the company configuration.

IMPORTANT:
- Do NOT click into individual job pages. Stay on the listing page.
- If you need to scroll to reveal more listings, do so before extracting.
- Use the extract_job_links tool to extract — do NOT use find_elements.
- In Case C, call report_no_matching_location_filter INSTEAD of extract_job_links. Do not fall back to extract_job_links in Case C.
- A closed dropdown/combobox labelled with a location-like term (Office, Location, Region, Country, City, ...) is a filter. Open it before deciding between Cases B and C.
- Scrolling alone does not expand collapsed sections — click each section header to reveal the listings it contains.
"""

# ---------------------------------------------------------------------------
# Golden: Case C tool description for COSTA_RICA_LATAM (pre-SYS-4 literal).
# ---------------------------------------------------------------------------
GOLDEN_NO_MATCH_DESCRIPTION: str = (
    "Report that this job board has a location or region filter, but "
    "the filter offers none of Costa Rica, CR, LATAM, or Latin America "
    "among its options. Call this INSTEAD of extract_job_links in that "
    "scenario (GOAL_PROMPT Case C). Requires no arguments. It finishes "
    "the run with an empty job list, signalling that this company has "
    "no listings applicable to the target region."
)


class TestGoalPromptGolden:
    """``GOAL_PROMPT`` must render byte-identical to the pre-SYS-4 literal."""

    def test_module_constant_matches_golden(self) -> None:
        assert GOAL_PROMPT == GOLDEN_GOAL_PROMPT

    def test_build_goal_prompt_costa_rica_latam_matches_golden(self) -> None:
        # ``GOAL_PROMPT`` is defined as ``build_goal_prompt(COSTA_RICA_LATAM)``,
        # so this is redundant with the previous test today — but it guards
        # against a future refactor that caches ``GOAL_PROMPT`` in some
        # other form and lets ``build_goal_prompt`` drift silently.
        assert build_goal_prompt(COSTA_RICA_LATAM) == GOLDEN_GOAL_PROMPT


class TestNoMatchDescriptionGolden:
    """The Case C tool description must render byte-identical to the pre-SYS-4 literal."""

    def test_build_no_match_description_costa_rica_latam_matches_golden(self) -> None:
        assert (
            build_no_match_description(COSTA_RICA_LATAM) == GOLDEN_NO_MATCH_DESCRIPTION
        )


class TestExpectedJobsNeverLeaksToPrompt:
    """SYS-9: ``expected_jobs`` MUST NOT enter the agent-visible surface.

    The verdict layer is a runtime *judge* — an out-of-band scoreboard
    that classifies a run after it finishes. Threading the human count
    into the prompt would turn the LLM into a target-driven optimiser
    (invent listings until you hit N, stop early once N is reached),
    contaminating every run's evidence value.

    This test locks the invariant on two independent axes:

    - **Source-level:** the substring ``expected_jobs`` appears
      nowhere in :mod:`job_agent_lab.navigation.prompt` — not in a
      clause constant, not in a comment, not in a docstring. The
      check is via :func:`inspect.getsource` so any refactor that
      moves the clauses into a different module also has to move
      the guard.
    - **Rendered-output-level:** the string ``expected_jobs`` does
      not appear in the rendered ``GOAL_PROMPT`` for the canonical
      region. Belt on top of the source check — the clause file could
      hypothetically pull a value in via ``str.format`` from a
      dynamic source, and this catches that class of regression at
      the rendered output.

    Neither check inspects the Case C tool description because the
    verdict layer has no rendering path into
    :func:`~job_agent_lab.navigation.controller.build_no_match_description`,
    and the golden test above already locks that surface byte-identical.
    """

    def test_expected_jobs_absent_from_prompt_module_source(self) -> None:
        import inspect

        import job_agent_lab.navigation.prompt as prompt_mod

        source = inspect.getsource(prompt_mod)
        assert "expected_jobs" not in source, (
            "expected_jobs leaked into the prompt module source. The "
            "verdict layer is out-of-band by design — never thread the "
            "human count through GOAL_PROMPT or its clause constants."
        )

    def test_expected_jobs_absent_from_rendered_goal_prompt(self) -> None:
        rendered = build_goal_prompt(COSTA_RICA_LATAM)
        assert "expected_jobs" not in rendered


# ---------------------------------------------------------------------------
# Golden: GOAL_PROMPT for COSTA_RICA_LATAM with filter_already_applied=True
# (SYS-12 Task 4). The rendered prompt inserts one NOTE clause between the
# intro paragraph and STEP 1; every other byte matches ``GOLDEN_GOAL_PROMPT``
# above.
# ---------------------------------------------------------------------------
GOLDEN_GOAL_PROMPT_FILTER_APPLIED: str = """You are on a company career page with a job board listing open positions. Your task is to return the URLs for job listings that apply to the target region (Costa Rica or Latin America).

NOTE: the location filter is already applied through the page URL; do NOT interact with any location filter control; treat the page as Case B already completed.

STEP 1 — FILTER (always start here):

1a. IDENTIFY. Scan the page for any location or region filter: a dropdown, combobox, checkbox group, tag list, or radio group whose label mentions location, region, country, city, office, or similar. A CLOSED combobox or dropdown still counts as a filter — do not skip it. A native HTML select element also counts as a filter — identify it by a name, id, or associated label containing tokens like country, location, region, office, city, pais, or país. A control whose name, id, and label mention none of those tokens is still the location filter if the options it offers include "Costa Rica", "CR", "LATAM", or "Latin America" — identify such a control by its option texts. Always interact with the control the page actually shows you: where a native select element is hidden behind a custom dropdown widget, use that widget's visible trigger and option list, not the hidden select.

1b. INSPECT. If a candidate filter exists, OPEN it (click the dropdown, expand the combobox, reveal the tag list) so its full option list is visible. You cannot decide between Case B and Case C without seeing the actual options.

1c. DECIDE. Handle exactly one of these three cases.

Case A — no location/region filter of any kind exists on the page. Go straight to STEP 2 and extract every listing.

Case B — the filter's options include "Costa Rica", "CR", "LATAM", or "Latin America". Select the first matching option, preferring "Costa Rica"/"CR" over "LATAM"/"Latin America" when both are offered. Filter options may be ISO country codes rather than full country names — treat "CR" or "CRI" as Costa Rica. If the filter sits in a form with a Search, Apply, Submit, or Filter button, click that button after selecting the option so the filter is applied. Then go to STEP 2 and extract the filtered listings.

Case C — the filter's options are visible, and none of "Costa Rica", "CR", "LATAM", or "Latin America" appear among them. Do NOT extract. Call the report_no_matching_location_filter tool. This finishes the run with an empty result, meaning the company has no listings applicable to the target region.

STEP 2 — EXTRACT (Cases A and B only):
Before extracting, if listings are grouped under collapsed section headers — for example headers labelled with a role count like "3 open roles", "5 positions", or "10 jobs", or an explicit Expand or Show affordance — click each such header first to reveal its listings. Do not expand unrelated accordions (FAQ, cookie banner, footer, help). Once the page is ready (filter applied if applicable, collapsed sections revealed, content loaded, scrolled to reveal all listings), call the extract_job_links tool. It takes no arguments — the tool already knows how to identify valid job link URLs based on the company configuration.

IMPORTANT:
- Do NOT click into individual job pages. Stay on the listing page.
- If you need to scroll to reveal more listings, do so before extracting.
- Use the extract_job_links tool to extract — do NOT use find_elements.
- In Case C, call report_no_matching_location_filter INSTEAD of extract_job_links. Do not fall back to extract_job_links in Case C.
- A closed dropdown/combobox labelled with a location-like term (Office, Location, Region, Country, City, ...) is a filter. Open it before deciding between Cases B and C.
- Scrolling alone does not expand collapsed sections — click each section header to reveal the listings it contains.
"""


class TestFilterAlreadyAppliedGolden:
    """SYS-12 conditional-clause switch on ``build_goal_prompt``.

    ``filter_already_applied=True`` inserts a single NOTE clause between
    the intro paragraph and STEP 1. The invariant guarded here is
    twofold:

    1. **True render is byte-identical to the frozen golden** — every
       byte outside the inserted clause must match the pre-SYS-12
       shape so future clause edits ripple through both goldens in the
       same commit and a byte-drift can never sneak in undocumented.
    2. **False render never carries the clause** — a regression that
       inadvertently forced the clause on would still pass the base
       golden test (which uses the default kwarg) but would leak the
       filter-applied instruction into every corpus company. The
       explicit False-branch assertion catches that class of bug.

    The base ``TestGoalPromptGolden`` class above already pins the
    False render byte-identical, so the two classes are complementary,
    not overlapping.
    """

    def test_filter_applied_render_matches_golden(self) -> None:
        rendered = build_goal_prompt(COSTA_RICA_LATAM, filter_already_applied=True)
        assert rendered == GOLDEN_GOAL_PROMPT_FILTER_APPLIED

    def test_filter_applied_render_contains_note_clause(self) -> None:
        rendered = build_goal_prompt(COSTA_RICA_LATAM, filter_already_applied=True)
        assert (
            "NOTE: the location filter is already applied through the page URL"
            in rendered
        )

    def test_default_render_omits_note_clause(self) -> None:
        # Regression guard: a wiring bug that inverts the flag would
        # still pass the base golden if the golden was regenerated,
        # but the NOTE substring must NEVER appear in the default
        # render.
        rendered = build_goal_prompt(COSTA_RICA_LATAM)
        assert "NOTE: the location filter is already applied" not in rendered

    def test_explicit_false_matches_default(self) -> None:
        assert build_goal_prompt(
            COSTA_RICA_LATAM, filter_already_applied=False
        ) == build_goal_prompt(COSTA_RICA_LATAM)
