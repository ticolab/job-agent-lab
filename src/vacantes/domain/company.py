"""Company schema: pydantic models describing a career-site test target.

``LinkRule`` is the declarative description of what a valid job link looks
like on the company's board. It currently carries ``path_prefix`` (SYS-1,
overrides the derived prefix when the default "strip the last path segment"
heuristic doesn't fit) and ``min_depth`` (SYS-3, requires the id-in-path
tail to be at least N segments deep — the fix for boards like Databricks
whose chrome links share the job-link prefix at depth 1 while real
postings live deeper).

``Company`` carries ``strategy`` (SYS-4, dispatches between the DOM
matcher and API adapters like Greenhouse) alongside the identity and
matcher inputs. The ``StrategyName`` ``Literal`` is the single
authoritative list of registered strategies — adding a new strategy
means extending this literal *and* registering an implementation in
:mod:`vacantes.extraction`; a unit test asserts the two stay in
sync.

``RuntimeHooks`` (SYS-12) is a nested frozen sub-model carrying four
opt-in per-board knobs that the deterministic side applies between
agent handoff and matcher invocation: ``pre_extract_css`` (extract-time
stylesheet injection to unhide anchors CSS-gated by a marketing class),
``expand_selector`` (bounded click-all rounds to open collapsed
accordions), ``next_control_selector`` (walker discovery bypass for
boards whose next-page affordance the generic signals miss), and
``filter_already_applied`` (a prompt-side conditional clause telling
the agent to skip location-filter interaction because the URL already
carries the filter). Every current corpus entry uses the default inert
value — a whitelist test guards against accidental opt-in — and two
cross-field validators enforce that non-inert hooks require
``strategy="dom"`` (the API path bypasses the deterministic hook
phase entirely) and that ``next_control_selector`` requires
``paginate=True`` (the override is only meaningful inside the walker).

``pre_filter_urls`` (SYS-13) is the agent-less multi-state escape hatch
for boards whose location filter can only be applied via URL and whose
target region maps to several such URLs (C18 — Plan A Technologies'
PeopleForce board exposes one ``?location_id=<n>`` URL per city, and the
Costa Rica target spans two cities). When non-empty, the DOM strategy
skips the browser-use agent entirely: one ``BrowserSession`` visits each
declared URL, runs the deterministic matcher (honoring
``paginate`` and ``hooks`` per state), and unions the resulting URL sets.
No ``OPENAI_API_KEY`` is consumed on this path. Two cross-field
validators enforce that non-empty ``pre_filter_urls`` requires
``strategy="dom"`` (the runner lives in :class:`DomStrategy`) and that
every declared URL shares origin with ``job_board_url`` (scheme+netloc
equality, which also rejects relative URLs for free). Every current
corpus entry declares the empty-tuple default; a whitelist test guards
against accidental opt-in.

Validation runs at import (``catalog.companies`` instantiates every entry),
so an invalid ``COMPANIES`` list — including a ``strategy="greenhouse"``
entry with a non-canonical ``job_board_url`` — fails loudly at startup
instead of during the extraction of the offending company.
"""

from __future__ import annotations

from typing import Literal, Self
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Canonical Greenhouse board hosts. ``job-boards.greenhouse.io`` is the
# current form; ``boards.greenhouse.io`` is the legacy host and still
# resolves for many tenants, so we accept both.
_GREENHOUSE_HOSTS: frozenset[str] = frozenset(
    {"job-boards.greenhouse.io", "boards.greenhouse.io"}
)

# PeopleForce serves every tenant from a subdomain of its platform
# domain, so ``strategy="peopleforce"`` is gated by a host check rather
# than by the presence of a per-tenant config object.
_PEOPLEFORCE_HOST_SUFFIX: str = ".peopleforce.io"

# BambooHR likewise serves every tenant from a subdomain of its own
# platform domain, so ``strategy="bamboohr"`` is gated by a host check
# rather than a per-tenant config object — the PeopleForce/Greenhouse
# precedent rather than the Phenom/Talentbrew/Coveo one.
_BAMBOOHR_HOST_SUFFIX: str = ".bamboohr.com"

# The authoritative list of extraction strategies. Every registered
# implementation in :mod:`vacantes.extraction.STRATEGIES` must map
# to a member here; a unit test enforces the two stay in sync so an
# unknown ``strategy=...`` value in the catalog is rejected at pydantic
# validation time (via ``Literal``) rather than at dispatch time.
StrategyName = Literal[
    "dom", "greenhouse", "phenom", "talentbrew", "coveo", "peopleforce", "bamboohr"
]


class LinkRule(BaseModel):
    """Declarative description of a valid job-link URL on a board.

    Attributes:
        path_prefix: Optional explicit override for the same-origin path
            prefix that job links must start with. When ``None``, the
            prefix is derived from ``Company.sample_job_url`` via
            ``extraction.dom.rules.derive_path_prefix``. Use an explicit
            value for boards whose URL shape does not fit the default
            "strip the last path segment" heuristic (e.g. Simplicant's
            ``/jobs/<id-slug>/detail``, Workday's ``/en-US/<site>/job``).
        min_depth: Minimum number of path segments the id-in-path tail
            must contain, counted from the segment immediately after
            ``path_prefix``. This is a floor, not an exact match: at the
            default of ``1`` every anchor whose path starts with
            ``<prefix>/`` is kept (byte-identical to the pre-SYS-3
            matcher). Raise it when the board's chrome links share the
            job-link prefix at shallow depths while real postings live
            deeper — Databricks (C9) has ``/company/careers/<team>``
            landing pages at depth 1 alongside ``/company/careers/<team>/<posting>``
            postings at depth 2, and Avionyx (C10+C9, same-origin iCIMS
            wrapper) has 4 chrome links at depth 1 under ``/jobs``
            alongside 16 real postings at depth 2. Only the id-in-path
            branch is gated; the id-in-query fallback (Akurey, 10Pearls
            shape) is unaffected because it never enters the depth check.
        suppress_ancestor_selector: Optional CSS selector identifying a
            container whose anchors are *never* job links (SYS-14, C16).
            During matching an anchor is dropped when
            ``anchor.closest(selector)`` returns non-null. The gate runs
            **after** the visibility gate and **before** URL bucketing,
            so it is orthogonal to visibility (a perfectly visible
            anchor inside the container is still dropped — that is the
            point) and a path bucket fully drained by suppression falls
            back to the id-in-query bucket exactly like a naturally
            empty one.

            Use it when a board renders a section whose anchors share
            the origin, ``path_prefix``, depth, and URL shape of the
            real postings, so no URL-layer discriminator exists.
            Ulteig (C16) is the canonical case: its UKG UltiPro board
            renders a personalized "Featured opportunities" block of
            ~20 recommendation anchors beside the real filtered
            results, all under the same prefix and the same
            ``?opportunityId=<uuid>`` id-in-query shape. UKG marks the
            wrapper with a platform-owned automation attribute, so
            ``suppress_ancestor_selector='[data-automation="featured-opportunities"]'``
            excludes the section without touching the results.

            ``closest()`` does not cross shadow or frame boundaries.
            That matches the matcher's per-document scan model: each
            scanned document (top document, open shadow root,
            same-origin frame) applies the gate independently, so an
            anchor inside a shadow root is *not* suppressed by a
            matching container that wraps its host in the light DOM.

            The value is per-company, exactly like ``path_prefix`` —
            deliberately no shared per-ATS default, even though the
            mechanism is UltiPro-family-generic. An invalid selector is
            a loud error rather than a silent no-op: the matcher
            validates it once per invocation and throws a named error.
            Selectors rot on board redesigns; the SYS-9 verdict layer
            is the designated detector.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path_prefix: str | None = None
    min_depth: int = Field(default=1, ge=1)
    suppress_ancestor_selector: str | None = None


class PhenomConfig(BaseModel):
    """Tenant parameters for the Phenom ``refineSearch`` API (SYS-15).

    Phenom People hosts its search widget on the *tenant's own* domain
    (BCG's lives at ``careers.bcg.com/widgets``), so unlike Greenhouse
    there is no platform hostname to validate against at import time.
    The presence of this config is therefore the loud-failure gate:
    ``strategy="phenom"`` requires it, and any other strategy forbids
    it (see the cross-field validators on :class:`Company`).

    Attributes:
        page_id: The tenant's Phenom page identifier, sent as the
            request body's ``pageId``. BCG's is ``"page17-ds"``. There
            is no way to derive it from the board URL — read it off a
            captured ``/widgets`` request when onboarding a tenant.
        locale: Sent as the body's ``lang``. Defaults to
            ``"en_global"``, the value BCG's board issues.
        endpoint_path: Path appended to the ``job_board_url`` origin to
            form the POST target. Defaults to ``"/widgets"``.

    The remaining request-body fields are not tenant-specific and live
    in the adapter's body builder
    (:func:`~vacantes.extraction.ats.phenom.build_request_body`),
    which is the single place the wire contract is expressed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    page_id: str
    locale: str = "en_global"
    endpoint_path: str = "/widgets"


class TalentbrewConfig(BaseModel):
    """Tenant parameters for the Talentbrew/Radancy results API (SYS-17).

    Talentbrew (also branded Radancy) is a self-hosted enterprise ATS:
    every tenant serves the search widget on its own domain (Citi's
    lives at ``jobs.citi.com/search-jobs``), so — exactly like Phenom
    — there is no platform hostname to allow-list at import time. The
    presence of this config is therefore the loud-failure gate:
    ``strategy="talentbrew"`` requires it, any other strategy forbids
    it (see the cross-field validators on :class:`Company`).

    The wire contract itself lives in the adapter's query builder
    (:func:`~vacantes.extraction.ats.talentbrew.build_query_params`),
    which is the single place the full parameter set is expressed. The
    two required fields below are the ones an integrator cannot
    reasonably discover without an evidence capture — the region facet
    id is an opaque per-tenant integer emitted only by the widget's
    own filter DOM, and its human display name doubles as the value of
    the ``FacetFilters[0].Display`` query parameter (Talentbrew's
    endpoint appears to ignore this string server-side, but we send
    the tenant's own value verbatim rather than a synthesised one).

    Attributes:
        facet_id: The tenant-specific region-facet identifier, sent as
            both ``ActiveFacetID`` and ``FacetFilters[0].ID``. Citi's
            Costa Rica facet is ``"3624060"``. Stored as ``str`` (not
            ``int``) because the query-string surface treats it as an
            opaque token and the value can be quoted / padded /
            non-numeric on other tenants — we forward it verbatim.
            Read it off one DevTools observation of an applied
            location-facet request.
        facet_display: The human-readable region name, sent as
            ``FacetFilters[0].Display`` alongside ``facet_id``. Citi's
            Costa Rica display is ``"Costa Rica"``. Kept required
            (not a default) so an integrator making a copy-paste
            mistake between tenants trips a validation error instead
            of shipping a request the endpoint answers but the
            regression tests cannot pin.
        records_per_page: Sent as ``RecordsPerPage``. Defaults to
            ``15`` — the value Citi's widget issues — with ``ge=1``
            so a fat-finger ``0`` fails loudly. Doubles as the
            adapter's continue-paginating threshold: the loop
            advances iff the LinkRule-filtered anchor count for the
            current page equals this value (and ``hasJobs=true``).
            Override only when a tenant's page size differs.
        results_path: Path appended to the ``job_board_url`` origin
            to form the GET target. Defaults to
            ``"/search-jobs/results"``, the Talentbrew default.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    facet_id: str
    facet_display: str
    records_per_page: int = Field(default=15, ge=1)
    results_path: str = "/search-jobs/results"


class CoveoConfig(BaseModel):
    """Tenant parameters for the Coveo search API (SYS-18).

    Coveo is a search *platform* rather than an ATS: the search
    endpoint itself is a platform constant
    (``https://{organization_id}.org.coveo.com/rest/search/v2``), but
    the **token-mint path is tenant-owned**, which is the load-bearing
    reason ``token_url`` is config rather than a derived constant. The
    P1 capture (``spike/evidence/ust_coveo_token.NOTES.txt``) pins
    this: there is no Coveo-platform-hosted anonymous-token endpoint
    to call — UST's own front door at
    ``https://www.ust.com/services/search`` proxies Coveo's
    ``/rest/search/token`` and answers anonymously, and the same notes
    record a *second* same-host variant
    (``/content/global/us/en/_jcr_content.token.json``) that the
    page falls back to on content-authoring paths. Per-tenant path
    variance is therefore guaranteed, so a shared constant would be
    wrong on the second tenant.

    As with Phenom and Talentbrew there is deliberately **no
    import-time host validator**: the mint path lives on the tenant's
    domain and the search host is derived from ``organization_id``, so
    presence of this config is the entire schema-time gate (see the
    cross-field validator on :class:`Company`). An origin coupling
    between ``token_url`` and ``job_board_url`` was considered and
    rejected — it would false-reject a tenant minting from a sibling
    subdomain.

    ``organization_id`` and ``search_hub`` are always required and
    neither is derivable from ``job_board_url``. The token source is a
    two-member union — exactly one of ``token_url`` or
    ``browser_token_key`` — enforced by the validator below. The wire
    contract itself lives in the adapter's body builder
    (:func:`~vacantes.extraction.ats.coveo.build_search_body`).

    Attributes:
        organization_id: The Coveo organization identifier. Doubles as
            the search host's leftmost label and as the
            ``organizationId`` query parameter, which is why it is not
            merely cosmetic config. UST's is
            ``"ustglobalproduction4ggrtx7v"``. Read it off the page's
            ``configureCloudV2Endpoint`` call or any captured search
            request.
        search_hub: Sent as the body's ``searchHub``. Selects the
            tenant's query pipeline server-side, so a wrong value
            silently returns a different result set rather than an
            error — which is why it is required rather than defaulted.
            UST's is ``"prod-jobs-search-hub"``.
        token_url: Absolute URL of the tenant's own token-mint
            endpoint, fetched once per run with ``GET`` and expected to
            answer ``{"token": "<JWT>"}``. UST's is
            ``"https://www.ust.com/services/search"``. Stored as a full
            URL (not a path appended to some origin) precisely because
            the mint path is tenant-owned and need not share
            ``job_board_url``'s origin. ``None`` on tenants whose mint
            is unreachable over HTTP — see ``browser_token_key``.
        browser_token_key: SYS-19 alternative token source: the
            ``sessionStorage`` key under which the tenant's own page
            stores the JWT it minted during a normal browser load. Set
            this *instead of* ``token_url`` when the mint origin sits
            behind fingerprint-based bot management (C19) and refuses
            every non-browser client. UST's is
            ``"searchToken_en_us"``.

            The distinction that makes this work is **read versus
            re-issue**. ``www.ust.com`` answers 403 to any ``httpx``
            request, and — per the C19 evidence — also to a
            ``page.evaluate`` fetch of the same URL from inside the
            already-loaded page. What is *not* blocked is reading the
            value the page's own XHR already deposited in
            ``sessionStorage``. The adapter therefore borrows the
            token rather than minting one.

            Per-tenant and not derivable: the key carries a locale
            suffix (``_en_us``), so a shared constant would be wrong on
            the second tenant — the same reasoning that makes
            ``token_url`` config.

            A pre-minted token supplied through config was considered
            and rejected: JWTs expire (24 h here), so it would decay
            into recurring manual maintenance, whereas a borrowed
            token is minted fresh by the page on every run.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    organization_id: str
    search_hub: str
    token_url: str | None = None
    browser_token_key: str | None = None

    @model_validator(mode="after")
    def _validate_exactly_one_token_source(self) -> Self:
        """Exactly one of ``token_url`` / ``browser_token_key`` (SYS-19).

        Both directions are errors for the same reason the rest of this
        module rejects unread config: the adapter reads whichever field
        is set and would silently ignore the other, so a tenant
        carrying both would ship with one of its two token sources
        dead and no signal saying which.

        Neither-set is rejected because the adapter cannot
        authenticate at all without a source, and failing at catalog
        import is strictly better than failing on the first live run.
        """
        has_url = self.token_url is not None
        has_key = self.browser_token_key is not None
        if has_url and has_key:
            raise ValueError(
                "CoveoConfig accepts exactly one token source; got both "
                f"token_url={self.token_url!r} and "
                f"browser_token_key={self.browser_token_key!r}. Use "
                "token_url for tenants whose mint answers HTTP clients, "
                "browser_token_key (UST: 'searchToken_en_us') for "
                "tenants behind bot management that refuse them."
            )
        if not has_url and not has_key:
            raise ValueError(
                "CoveoConfig requires a token source: set either "
                "token_url (the tenant's mint endpoint, e.g. "
                "'https://www.ust.com/services/search') or "
                "browser_token_key (the sessionStorage key holding the "
                "page-minted JWT, e.g. 'searchToken_en_us')."
            )
        return self


class RuntimeHooks(BaseModel):
    """Per-board deterministic page-preparation knobs (SYS-12).

    Every field is opt-in and defaults to a value that leaves the
    pre-SYS-12 code paths byte-identical. Non-inert hooks are executed
    by the deterministic collector (never the agent) between agent
    handoff and matcher invocation, in the order pinned by the
    architecture proposal §4.5: ``pre_extract_css`` inject → expansion
    rounds → matcher (or walker, with ``next_control_selector`` as the
    discovery override). ``filter_already_applied`` is the sole
    prompt-side hook — it renders a conditional clause into
    ``build_goal_prompt`` telling the agent the location filter is
    already carried by the URL, so it must skip filter interaction.

    Attributes:
        expand_selector: CSS selector for a repeatable "expand" affordance
            (e.g. ``button[aria-expanded="false"][data-role="accordion"]``).
            When set, the collector runs bounded click-all rounds via
            ``expand_all`` before invoking the matcher: each round stamps
            every visible match with an indexed ``data-jal-expand`` marker
            and clicks each stamp through the ``PageDriver.click`` seam,
            polls the settle interval, and re-queries; the loop
            terminates on a zero-stamp round or at
            ``EXPAND_MAX_ROUNDS``. Motivating board: Deel (C4 residual)
            —  ``button[aria-expanded="false"][class*="hover:bg-neutral-50"]``.
            Runs against state 1 only (documented, mirroring the
            frame-walk limit); the flag ⟷ ``--paginate`` mutex on the
            capture script does *not* apply to catalog-sourced hooks,
            so a hook-configured expand can legally combine with
            ``paginate=True`` — expansion still runs once, against
            state 1.
        next_control_selector: CSS selector for the walker's next-page
            control on boards where the generic signals in
            ``find_next_control.js`` misfire or miss the real affordance.
            When set (and ``paginate=True``), the discovery asset
            skips signals 1–6 entirely and stamps the first
            ``querySelector(<selector>)`` match; the walker's loop
            shape (find → click marker → settle) is otherwise
            unchanged, so a missing match terminates the walk
            normally. Motivating board: Hire With Near — an
            append-in-place ``Load more`` button whose label matches
            none of the walker's generic signals (``rel=next``,
            aria-label ``/next/i``, text ``/^next$/i``, numeric
            successor), so discovery misses it entirely and the board
            collects only its first 20 of 214 anchors. The button
            carries no id, no ``data-*`` attribute, and no
            aria-label, so the selector keys on its Tailwind
            important-modifier class
            (``button.\\!bg-primary``), which resolves uniquely on
            the board today; a redesign that restyles it is caught at
            the SYS-9 verdict layer as an ``under``.
        filter_already_applied: When ``True``, ``build_goal_prompt``
            renders a conditional clause between the intro and Step 1a
            telling the agent the location filter is already carried by
            the URL and it must not interact with any location filter
            control (treat the page as Case B already completed). This
            is the sole prompt-side hook; the collector never inspects
            this field. Motivating board: Progress (C11) — pre-filtered
            ``?location=Costa+Rica`` URL where the visible filter chrome
            would otherwise re-trip the agent's Case-B playbook.
        pre_extract_css: A stylesheet payload injected into the top
            document's ``<head>`` as ``<style data-jal-css>`` before
            expansion and matcher invocation. Idempotent — re-running
            replaces the existing ``data-jal-css`` element. Invalid
            payloads that parse to zero rules raise loudly (never a
            silent no-op); a partially valid stylesheet passes because
            CSS parsers drop invalid rules individually. Scope is
            light DOM / top document, matching the walker and expander;
            in SPA-paginated boards where the document persists across
            states the injected ``<style>`` survives, but a hard
            navigation between states blows it away (documented
            limitation). Motivating board: SentinelOne (C17) — anchors
            hidden by a Tailwind ``.md:hidden`` class-gated rule,
            unhidden by ``.md\\:hidden{display:block!important}``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    expand_selector: str | None = None
    next_control_selector: str | None = None
    filter_already_applied: bool = False
    pre_extract_css: str | None = None

    @property
    def is_inert(self) -> bool:
        """Return ``True`` iff every field equals the inert default.

        Downstream code (collector, capture, tests) gates on this
        property instead of comparing against ``RuntimeHooks()``
        directly or unpacking individual fields, so the "no hooks"
        idiom lives in exactly one place. Deliberately a plain
        ``@property`` and not a ``@computed_field``: it must not leak
        into ``model_dump`` / serialization, and the per-check
        allocation is irrelevant at the collector's call frequency.
        """
        return self == RuntimeHooks()


class Company(BaseModel):
    """A career site in the corpus.

    Attributes:
        name: Canonical display name, also used to derive the acronym
            handle (``Growth Acceleration Partners`` → ``gap``) and the
            slug for output filenames and snapshot directories.
        aliases: Extra short handles for the ``-c`` flag. The CLI already
            matches acronym and name substring, so aliases are only needed
            for nicknames neither of those covers.
        job_board_url: The listing page the agent navigates to (DOM
            strategy) or the canonical API board URL (API strategies).
            For ``strategy="greenhouse"`` this must be
            ``https://job-boards.greenhouse.io/<board_token>`` or
            ``https://boards.greenhouse.io/<board_token>``; the
            ``<board_token>`` is not always derivable from a marketing
            domain (West Monroe's board token is ``westmonroe4``), so
            we require it to be spelled out at catalog-authoring time.
        sample_job_url: An example individual job URL, used to derive the
            path prefix that identifies valid job links (unless
            ``link_rule.path_prefix`` overrides it). API strategies
            ignore this field.
        link_rule: The link-shape rule used to identify job anchors on
            the rendered listing page. Defaults to ``LinkRule()`` — the
            path prefix is derived automatically from ``sample_job_url``.
            API strategies ignore this field.
        strategy: Which extraction strategy handles this company. The
            default ``"dom"`` runs the browser-use agent + deterministic
            matcher; ``"greenhouse"`` (SYS-4 Task 3) hits the Greenhouse
            job-boards API and filters by
            :meth:`~vacantes.domain.region.TargetRegion.matches`.
        paginate: SYS-5 opt-in for boards whose full listing is split
            across multiple DOM states advanced by clicking an in-page
            "next" control (Techwarely, BCG). When ``True`` and
            ``strategy="dom"``, the collector delegates to
            :func:`~vacantes.extraction.dom.collector.walk_and_collect`,
            which discovers the next-page affordance via generic signals
            (``rel=next``, aria-label ``/next/i``, text ``/^next$/i``,
            numeric successor), clicks the stamped ``[data-jal-next]``
            marker driver-side, and unions matcher results across states
            until no control is found, a click yields no new links, or
            ``MAX_PAGES=20`` is hit. Defaults to
            ``False`` — every corpus company today collects in a single
            pass and is byte-identical to the pre-SYS-5 behaviour. Only
            meaningful for ``strategy="dom"``; the Greenhouse API
            strategy pages internally at the request level.
        expected_jobs: SYS-9 human-counted, region-filtered live target
            from the integration queue. ``None`` (the default) means the
            entry has never been counted — every catalog entry that
            predates SYS-9 carries ``None``, no backfill is done. ``0``
            is a legitimate value: a Case-C board that has no listings
            in the target region has a well-defined expectation of zero.
            This value is *never* adjusted to match observed reality —
            :func:`~vacantes.extraction.base.build_report` compares
            it against ``total_jobs_found`` and emits a
            ``metadata.verdict`` key
            (``match``/``under``/``over``/``unverified``) that
            :func:`~vacantes.reporting.output.print_summary`
            renders, and that ``--strict`` gates the CLI exit code on.
            Deliberately absent from any agent-visible surface (never
            interpolated into ``extraction.dom.agent.prompt``) so the LLM cannot
            curve-fit to the count.
        hooks: SYS-12 nested :class:`RuntimeHooks` bag of opt-in per-board
            page-preparation knobs. Defaults to ``RuntimeHooks()`` — the
            inert value that leaves every pre-SYS-12 code path
            byte-identical. See :class:`RuntimeHooks` for per-field
            semantics and board exemplars. Two cross-field validators
            below enforce (a) non-inert hooks require ``strategy="dom"``
            (the Greenhouse API path bypasses the deterministic hook
            phase entirely) and (b) ``next_control_selector`` requires
            ``paginate=True`` (the override is only meaningful inside
            the walker).
        pre_filter_urls: SYS-13 opt-in for boards whose location filter
            can only be applied via URL and whose target region maps to
            several such URLs. Defaults to ``()`` — the empty tuple that
            leaves every pre-SYS-13 code path byte-identical (the
            ``DomStrategy.extract`` dispatch branch is a no-op when this
            field is empty). When non-empty, ``DomStrategy`` skips the
            browser-use agent entirely: one ``BrowserSession`` visits
            each URL in order, runs ``collect_job_links`` per state
            (honoring ``paginate`` and ``hooks`` per state — hooks
            re-apply on each hard navigation), and unions the resulting
            URL sets before reporting through ``build_report`` with
            ``None`` agent-fields plus ``metadata.states_visited``.
            Motivating board: Plan A Technologies (C18 in
            ``blockers/INTEGRATION_BLOCKERS.md``) — PeopleForce
            exposes one ``?location_id=<n>`` URL per city and the Costa
            Rica target spans Cartago (63939) and Heredia (51094);
            declaring both URLs here lets the runner union the two
            filtered lists deterministically without an LLM in the
            loop, closing C18 structurally. No ``OPENAI_API_KEY`` is
            consumed on this path — the agent is never constructed.
            Fragility of the declared URLs (a city id changing upstream,
            a query-string schema change) is caught at the SYS-9
            verdict layer: a stale URL that no longer matches any
            postings degrades to zero anchors for that state, and the
            union count falls below ``expected_jobs`` producing a
            ``verdict="under"``. Two cross-field validators below
            enforce (a) non-empty ``pre_filter_urls`` requires
            ``strategy="dom"`` (the runner lives in :class:`DomStrategy`
            and the Greenhouse API path never navigates URLs) and (b)
            every declared URL shares origin with ``job_board_url``
            (scheme+netloc equality via :func:`urllib.parse.urlparse`,
            which also rejects relative URLs for free). Kept as a
            ``tuple`` (frozen, hashable) matching the ``aliases`` field
            convention. Note: ``hooks.filter_already_applied`` alongside
            non-empty ``pre_filter_urls`` is *not* rejected — the flag
            is prompt-side only and is simply never read on the
            agent-less path.
        phenom: SYS-15 tenant config for ``strategy="phenom"``. ``None``
            (the default) for every other strategy — a cross-field
            validator enforces both directions, and that presence check
            is the *entire* schema-time gate for this strategy because
            Phenom is tenant-co-hosted and offers no platform hostname
            to allow-list (contrast the Greenhouse host validator).
            Motivating board: BCG (C12 in
            ``blockers/INTEGRATION_BLOCKERS.md``), whose country
            facet is sessionStorage state rather than a URL parameter
            and whose 863-posting unfiltered board dwarfs the walker's
            ``MAX_PAGES`` — so any DOM snapshot count would be a
            walker-cap artifact. Routing it through the API closes C12
            by giving it an honest regression artifact: a recorded
            payload fixture instead of a frozen ``page.html``. Note
            that ``strategy="phenom"`` additionally requires
            ``link_rule.path_prefix`` to be set, because the adapter
            synthesizes posting URLs from it; that requirement is
            enforced at extract time rather than here (the payload
            carries no board URL to validate against at import).
        talentbrew: SYS-17 tenant config for ``strategy="talentbrew"``.
            ``None`` (the default) for every other strategy — a
            cross-field validator enforces both directions, and that
            presence check is the *entire* schema-time gate for this
            strategy because Talentbrew (a.k.a. Radancy) is a
            self-hosted enterprise ATS: every tenant serves the widget
            on its own domain (Citi's is ``jobs.citi.com``), so there
            is no platform hostname to allow-list. Motivating board:
            Citi (C12 in ``blockers/INTEGRATION_BLOCKERS.md`` — a
            3,529-posting unfiltered board where the walker cap would
            hit long before the region facet, and the facet is not
            URL-addressable at the page level). Routing it through the
            tenant's own ``/search-jobs/results`` endpoint closes C12
            with a recorded API-payload fixture, mirroring the
            Phenom/Greenhouse regression shape. Note that
            ``strategy="talentbrew"`` additionally requires
            ``link_rule.path_prefix`` to be set explicitly, because
            derivation from the depth-4
            ``/job/<city>/<slug>/<career-site-id>/<job-id>`` sample URL
            yields the too-specific ``/job/heredia/<slug>/287`` — that
            requirement is enforced at extract time rather than here.
        coveo: SYS-18 tenant config for ``strategy="coveo"``. ``None``
            (the default) for every other strategy — a cross-field
            validator enforces both directions, and that presence
            check is the *entire* schema-time gate: the search host is
            derived from ``organization_id`` and the token-mint path is
            tenant-owned, so there is no platform hostname to
            allow-list. Motivating board: UST (the C1 half in
            ``blockers/INTEGRATION_BLOCKERS.md``), a React/Coveo SPA
            that renders **zero** job anchors at any viewport — the
            postings exist only in the search XHR's ``clickUri``
            fields, so no matcher extension can reach them. Unlike the
            Phenom and Talentbrew entries, this strategy does **not**
            need ``link_rule.path_prefix``: posting URLs are read
            verbatim from the payload rather than synthesized or
            URL-filtered.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    aliases: tuple[str, ...] = ()
    job_board_url: str
    sample_job_url: str
    link_rule: LinkRule = LinkRule()
    strategy: StrategyName = "dom"
    paginate: bool = False
    expected_jobs: int | None = Field(default=None, ge=0)
    hooks: RuntimeHooks = RuntimeHooks()
    pre_filter_urls: tuple[str, ...] = ()
    phenom: PhenomConfig | None = None
    talentbrew: TalentbrewConfig | None = None
    coveo: CoveoConfig | None = None

    @model_validator(mode="after")
    def _validate_phenom_config_presence(self) -> Self:
        """``strategy="phenom"`` ⇔ ``phenom`` config present (SYS-15).

        Both directions are enforced. A ``strategy="phenom"`` entry
        without the config cannot build a request body at all (there is
        no derivable ``page_id``), and a config attached to any other
        strategy is dead weight that would silently never be read —
        exactly the "misconfig fails loudly at import" property the
        Greenhouse host validator established.

        Note the deliberate *absence* of a host validator here. Phenom
        is tenant-co-hosted (BCG's widget lives on
        ``careers.bcg.com``), so there is no platform hostname to
        allow-list; config presence is the entire schema-time gate.
        """
        if self.strategy == "phenom" and self.phenom is None:
            raise ValueError(
                "strategy='phenom' requires a phenom=PhenomConfig(...) "
                f"on company={self.name!r}. The tenant's page_id cannot "
                "be derived from job_board_url — read it off a captured "
                "/widgets request (BCG's is 'page17-ds')."
            )
        if self.strategy != "phenom" and self.phenom is not None:
            raise ValueError(
                f"phenom=PhenomConfig(...) is set on company={self.name!r} "
                f"but strategy={self.strategy!r}. The config is only read "
                "by the Phenom adapter; either set strategy='phenom' or "
                "drop the config."
            )
        return self

    @model_validator(mode="after")
    def _validate_talentbrew_config_presence(self) -> Self:
        """``strategy="talentbrew"`` ⇔ ``talentbrew`` config present (SYS-17).

        Both directions are enforced. A ``strategy="talentbrew"`` entry
        without the config cannot build a request at all — the tenant's
        ``facet_id`` and ``facet_display`` are not derivable from
        ``job_board_url`` and must be read off a captured
        ``/search-jobs/results`` request — and a config attached to
        any other strategy is dead weight that would silently never be
        read. Mirrors :meth:`_validate_phenom_config_presence` verbatim
        (both strategies are tenant-co-hosted, so config presence is
        the entire schema-time gate; there is no platform hostname to
        allow-list — the Greenhouse-style host validator has no
        equivalent for Talentbrew).
        """
        if self.strategy == "talentbrew" and self.talentbrew is None:
            raise ValueError(
                "strategy='talentbrew' requires a "
                "talentbrew=TalentbrewConfig(...) on "
                f"company={self.name!r}. The tenant's facet_id and "
                "facet_display cannot be derived from job_board_url "
                "— read them off a captured /search-jobs/results "
                "request (Citi's Costa Rica facet is "
                "facet_id='3624060', facet_display='Costa Rica')."
            )
        if self.strategy != "talentbrew" and self.talentbrew is not None:
            raise ValueError(
                f"talentbrew=TalentbrewConfig(...) is set on "
                f"company={self.name!r} but strategy={self.strategy!r}. "
                "The config is only read by the Talentbrew adapter; "
                "either set strategy='talentbrew' or drop the config."
            )
        return self

    @model_validator(mode="after")
    def _validate_coveo_config_presence(self) -> Self:
        """``strategy="coveo"`` ⇔ ``coveo`` config present (SYS-18).

        Both directions are enforced, mirroring the Phenom and
        Talentbrew validators. A ``strategy="coveo"`` entry without the
        config cannot even address the search endpoint — the host is
        built from ``organization_id`` — nor mint a token, since the
        mint path is tenant-owned and therefore not derivable. A config
        attached to any other strategy is dead weight that would
        silently never be read.

        As with its two siblings there is deliberately no host
        validator: config presence is the entire schema-time gate.
        """
        if self.strategy == "coveo" and self.coveo is None:
            raise ValueError(
                "strategy='coveo' requires a coveo=CoveoConfig(...) on "
                f"company={self.name!r}. The organization_id, search_hub, "
                "and tenant-owned token_url cannot be derived from "
                "job_board_url — read them off the page's "
                "configureCloudV2Endpoint call or a captured search "
                "request (UST's are "
                "organization_id='ustglobalproduction4ggrtx7v', "
                "search_hub='prod-jobs-search-hub', "
                "token_url='https://www.ust.com/services/search')."
            )
        if self.strategy != "coveo" and self.coveo is not None:
            raise ValueError(
                f"coveo=CoveoConfig(...) is set on company={self.name!r} "
                f"but strategy={self.strategy!r}. The config is only read "
                "by the Coveo adapter; either set strategy='coveo' or "
                "drop the config."
            )
        return self

    @model_validator(mode="after")
    def _validate_greenhouse_host(self) -> Self:
        """Enforce the Greenhouse job-board URL convention.

        When ``strategy="greenhouse"``, ``job_board_url`` must be a
        canonical Greenhouse board URL — a known Greenhouse host plus a
        non-empty first path segment (the board token). This validator
        is the *belt*; the actual token-extraction helper the runtime
        uses (SYS-4 Task 3) is the braces.

        Non-``greenhouse`` strategies bypass this check entirely — the
        DOM strategy validates URLs at runtime through same-origin
        matcher checks and does not benefit from a schema-time host
        allow-list.
        """
        if self.strategy != "greenhouse":
            return self

        parsed = urlparse(self.job_board_url)
        host = parsed.hostname
        if host not in _GREENHOUSE_HOSTS:
            allowed = ", ".join(sorted(_GREENHOUSE_HOSTS))
            raise ValueError(
                f"strategy='greenhouse' requires job_board_url host to be "
                f"one of ({allowed}); got host={host!r} in "
                f"{self.job_board_url!r}. Set job_board_url to the "
                f"canonical Greenhouse board URL "
                f"(https://job-boards.greenhouse.io/<board_token>)."
            )

        segments = [s for s in parsed.path.split("/") if s]
        if not segments:
            raise ValueError(
                f"strategy='greenhouse' requires a board token in "
                f"job_board_url's path (e.g. "
                f"https://job-boards.greenhouse.io/<board_token>); "
                f"got path={parsed.path!r} in {self.job_board_url!r}."
            )
        return self

    @model_validator(mode="after")
    def _validate_peopleforce_host(self) -> Self:
        """Enforce the PeopleForce board-URL convention.

        ``strategy="peopleforce"`` requires ``job_board_url`` to sit on
        a tenant subdomain of the platform domain. PeopleForce hosts
        every tenant itself, so — unlike the tenant-co-hosted Phenom /
        Talentbrew / Coveo adapters, whose entire schema-time gate is
        the presence of a config object — there is a real platform
        hostname to allow-list, and the adapter needs no per-tenant
        config at all: the tenant subdomain *is* the identity, and the
        region's location ids are discovered from the board at run
        time rather than declared. This follows the Greenhouse
        precedent.

        The bare platform domain is rejected alongside foreign hosts:
        ``peopleforce.io`` on its own is the vendor's marketing site,
        not a board.
        """
        if self.strategy != "peopleforce":
            return self

        host = urlparse(self.job_board_url).hostname or ""
        if not host.endswith(_PEOPLEFORCE_HOST_SUFFIX):
            raise ValueError(
                f"strategy='peopleforce' requires job_board_url to be on a "
                f"tenant subdomain of {_PEOPLEFORCE_HOST_SUFFIX.lstrip('.')}; "
                f"got host={host!r} in {self.job_board_url!r}. Point "
                f"job_board_url at the tenant board "
                f"(https://<tenant>{_PEOPLEFORCE_HOST_SUFFIX}/careers), not at "
                f"the company's marketing site."
            )
        return self

    @model_validator(mode="after")
    def _validate_bamboohr_host(self) -> Self:
        """Enforce the BambooHR board-URL convention.

        ``strategy="bamboohr"`` requires ``job_board_url`` to sit on a
        tenant subdomain of the platform domain, mirroring
        :meth:`_validate_peopleforce_host`. BambooHR hosts every tenant
        itself and the adapter needs no per-tenant config — the tenant
        subdomain *is* the identity, and the ``/careers/list`` endpoint
        is a platform constant — so a host allow-list is the whole
        schema-time gate.

        The bare platform domain is rejected alongside foreign hosts:
        ``bamboohr.com`` on its own is the vendor's marketing site.

        Note this validator is deliberately scoped to
        ``strategy="bamboohr"`` only. Two corpus entries (Gorilla Logic,
        Chainstack) sit on BambooHR hosts while running the DOM
        strategy; they are untouched by this rule and keep working
        exactly as before.
        """
        if self.strategy != "bamboohr":
            return self

        host = urlparse(self.job_board_url).hostname or ""
        if not host.endswith(_BAMBOOHR_HOST_SUFFIX):
            raise ValueError(
                f"strategy='bamboohr' requires job_board_url to be on a "
                f"tenant subdomain of {_BAMBOOHR_HOST_SUFFIX.lstrip('.')}; "
                f"got host={host!r} in {self.job_board_url!r}. Point "
                f"job_board_url at the tenant board "
                f"(https://<tenant>{_BAMBOOHR_HOST_SUFFIX}/careers), not at "
                f"the company's marketing site."
            )
        return self

    @model_validator(mode="after")
    def _validate_hooks_require_dom_strategy(self) -> Self:
        """Non-inert :class:`RuntimeHooks` require ``strategy="dom"``.

        The deterministic hook phase (css inject → expansion →
        matcher / walker) is executed by :mod:`extraction.dom.collector`;
        the Greenhouse API strategy hits the boards API directly and
        never renders a page, so any hook set on a
        ``strategy="greenhouse"`` entry would be silently ignored. We
        make that a schema-time error so the mismatch is caught at
        catalog import rather than by an integrator wondering why their
        ``pre_extract_css`` never fired. The error message names the
        specific hook fields set so the fix is a one-line unset.

        Inert hooks (the default) bypass this check on any strategy.
        """
        if self.hooks.is_inert or self.strategy == "dom":
            return self

        set_fields = sorted(
            name
            for name, default in (
                ("expand_selector", None),
                ("next_control_selector", None),
                ("filter_already_applied", False),
                ("pre_extract_css", None),
            )
            if getattr(self.hooks, name) != default
        )
        raise ValueError(
            f"RuntimeHooks require strategy='dom'; got "
            f"strategy={self.strategy!r} with non-inert hooks "
            f"{set_fields!r}. Either drop the hooks or switch the "
            f"entry to strategy='dom'."
        )

    @model_validator(mode="after")
    def _validate_next_control_selector_requires_paginate(self) -> Self:
        """``hooks.next_control_selector`` is only meaningful when paginating.

        The override is threaded into
        :func:`~vacantes.extraction.dom.collector.walk_and_collect`
        and stamps the walker's next-page marker; the single-shot
        matcher path never invokes it. Setting the selector without
        ``paginate=True`` would therefore be a silent no-op — same
        rationale as the hooks-require-dom validator above, caught at
        catalog import time.
        """
        if self.hooks.next_control_selector is not None and not self.paginate:
            raise ValueError(
                f"hooks.next_control_selector="
                f"{self.hooks.next_control_selector!r} requires "
                f"paginate=True; the walker is the only consumer of "
                f"the override, so setting it on a single-shot entry "
                f"would be silently ignored."
            )
        return self

    @model_validator(mode="after")
    def _validate_pre_filter_urls_require_dom_strategy(self) -> Self:
        """Non-empty ``pre_filter_urls`` requires ``strategy="dom"``.

        The agent-less multi-state runner lives in the DOM strategy
        (:class:`~vacantes.extraction.dom.strategy.DomStrategy`);
        the Greenhouse API strategy hits the boards API directly and
        never navigates URLs, so ``pre_filter_urls`` would be silently
        ignored. Reject at catalog-import time so the mismatch surfaces
        at startup rather than at extraction time. Mirrors the SYS-12
        ``hooks``-require-``dom`` rule; same error-message style.

        Empty tuple (the default) bypasses this check on any strategy.
        """
        if not self.pre_filter_urls or self.strategy == "dom":
            return self
        raise ValueError(
            f"pre_filter_urls requires strategy='dom'; got "
            f"strategy={self.strategy!r} with "
            f"pre_filter_urls={self.pre_filter_urls!r}. Either drop "
            f"the URLs or switch the entry to strategy='dom'."
        )

    @model_validator(mode="after")
    def _validate_pre_filter_urls_same_origin(self) -> Self:
        """Every ``pre_filter_urls`` entry must share origin with ``job_board_url``.

        The DOM matcher is same-origin by construction — anchors on a
        state document that point at a different origin are structurally
        rejected downstream, and a state URL pointing at a different
        origin than ``job_board_url`` would additionally violate the
        "one board per Company" invariant (base-href resolution, the
        ``origin`` argument threaded into the matcher, snapshot
        ``top_url`` semantics) that the rest of the runtime assumes.
        Compare ``(scheme, netloc)`` via :func:`urllib.parse.urlparse`,
        which also rejects relative URLs (empty ``scheme``/``netloc``)
        for free — a relative state URL would fail base-href resolution
        at navigation time and the error is more useful surfaced here
        at catalog-import time.
        """
        if not self.pre_filter_urls:
            return self
        board = urlparse(self.job_board_url)
        board_origin = (board.scheme, board.netloc)
        for url in self.pre_filter_urls:
            parsed = urlparse(url)
            if (parsed.scheme, parsed.netloc) != board_origin:
                raise ValueError(
                    f"pre_filter_urls entries must share origin "
                    f"(scheme+netloc) with "
                    f"job_board_url={self.job_board_url!r}; got "
                    f"mismatching entry {url!r}."
                )
        return self
