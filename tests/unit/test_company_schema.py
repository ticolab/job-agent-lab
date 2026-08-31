"""Unit tests for the ``Company`` / ``LinkRule`` pydantic schema and the
integrity of the production ``COMPANIES`` catalog.

The schema tests lock the validation contract at the Company/LinkRule
level, including the SYS-4 ``strategy`` field and its Greenhouse-host
model validator. The catalog integrity tests protect against silent
corruption of the shipped list — duplicated entries, empty required
fields, and broken snapshot ↔ catalog cross-references. Together they
close the loop that used to be enforced only implicitly by the
pre-SYS-1 ``TypedDict`` shape plus manual review.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from job_agent_lab.catalog import COMPANIES, slugify
from job_agent_lab.domain.company import (
    Company,
    CoveoConfig,
    LinkRule,
    PhenomConfig,
    RuntimeHooks,
    TalentbrewConfig,
)


class TestCompanySchema:
    """Pydantic-level validation on the ``Company`` model."""

    def test_minimal_valid_company(self) -> None:
        c = Company(
            name="Example",
            job_board_url="https://example.com/careers",
            sample_job_url="https://example.com/jobs/1",
        )
        assert c.aliases == ()
        assert c.link_rule == LinkRule()
        assert c.link_rule.path_prefix is None

    def test_missing_name_raises(self) -> None:
        with pytest.raises(ValidationError):
            Company(  # type: ignore[call-arg]
                job_board_url="https://example.com/careers",
                sample_job_url="https://example.com/jobs/1",
            )

    def test_missing_job_board_url_raises(self) -> None:
        with pytest.raises(ValidationError):
            Company(  # type: ignore[call-arg]
                name="Example",
                sample_job_url="https://example.com/jobs/1",
            )

    def test_missing_sample_job_url_raises(self) -> None:
        with pytest.raises(ValidationError):
            Company(  # type: ignore[call-arg]
                name="Example",
                job_board_url="https://example.com/careers",
            )

    def test_extra_field_forbidden(self) -> None:
        # ``extra="forbid"`` — typos like ``path_prefix`` at the Company
        # level (instead of nested under ``link_rule``) fail loudly at
        # import time rather than silently being ignored.
        with pytest.raises(ValidationError):
            Company(
                name="Example",
                job_board_url="https://example.com/careers",
                sample_job_url="https://example.com/jobs/1",
                path_prefix="/jobs",  # type: ignore[call-arg]
            )

    def test_frozen(self) -> None:
        # ``frozen=True`` — Company instances are hashable and immutable,
        # which lets the catalog be treated as a value-object table.
        c = Company(
            name="Example",
            job_board_url="https://example.com/careers",
            sample_job_url="https://example.com/jobs/1",
        )
        with pytest.raises(ValidationError):
            c.name = "Renamed"  # type: ignore[misc]


class TestLinkRuleSchema:
    """Pydantic-level validation on the ``LinkRule`` model."""

    def test_default_path_prefix_is_none(self) -> None:
        assert LinkRule().path_prefix is None

    def test_explicit_path_prefix(self) -> None:
        assert LinkRule(path_prefix="/jobs").path_prefix == "/jobs"

    def test_default_min_depth_is_one(self) -> None:
        # Byte-identical-to-pre-SYS-3 default: every anchor under
        # ``<prefix>/`` is kept, no floor applied.
        assert LinkRule().min_depth == 1

    def test_explicit_min_depth(self) -> None:
        assert LinkRule(min_depth=2).min_depth == 2

    def test_min_depth_zero_raises(self) -> None:
        # ``ge=1`` — depth 0 would mean "match the prefix itself", which
        # is the id-in-query branch and lives outside this floor.
        with pytest.raises(ValidationError):
            LinkRule(min_depth=0)

    def test_min_depth_negative_raises(self) -> None:
        with pytest.raises(ValidationError):
            LinkRule(min_depth=-1)

    def test_min_depth_frozen(self) -> None:
        rule = LinkRule(min_depth=2)
        with pytest.raises(ValidationError):
            rule.min_depth = 3  # type: ignore[misc]

    def test_default_suppress_ancestor_selector_is_none(self) -> None:
        # SYS-14 default: no container suppression, byte-identical to
        # the pre-SYS-14 matcher (the JS gate is skipped entirely when
        # the fourth argument is null).
        assert LinkRule().suppress_ancestor_selector is None

    def test_explicit_suppress_ancestor_selector(self) -> None:
        # The Ulteig (C16) value — a platform-owned UKG automation
        # marker. Stored verbatim; the matcher does the interpreting.
        selector = '[data-automation="featured-opportunities"]'
        assert LinkRule(
            suppress_ancestor_selector=selector
        ).suppress_ancestor_selector == (selector)

    def test_suppress_ancestor_selector_frozen(self) -> None:
        rule = LinkRule(suppress_ancestor_selector=".featured")
        with pytest.raises(ValidationError):
            rule.suppress_ancestor_selector = ".other"  # type: ignore[misc]

    def test_suppress_ancestor_selector_is_not_validated_at_schema_time(
        self,
    ) -> None:
        # Deliberate: CSS-selector validity is a *browser* question, so
        # the check lives in the matcher asset (which throws a named
        # error on an invalid selector — see
        # ``tests/snapshots/test_matcher_rules.py``). Pydantic accepts
        # any string, exactly like ``path_prefix`` accepts any string
        # without checking it against the board. Pinning this keeps a
        # future contributor from adding a half-hearted regex validator
        # here that would diverge from the browser's parser.
        assert (
            LinkRule(suppress_ancestor_selector="[unclosed").suppress_ancestor_selector
            == "[unclosed"
        )

    def test_extra_field_forbidden(self) -> None:
        # ``max_depth`` is on the SYS-3 "deliberately not added" list;
        # this guards against it being added by accident and also serves
        # as the rejected-extra-field case now that ``min_depth`` is a
        # real field.
        with pytest.raises(ValidationError):
            LinkRule(max_depth=3)  # type: ignore[call-arg]

    def test_suppress_ancestor_selector_corpus_entries_are_whitelisted(self) -> None:
        # SYS-14 landed with strict "no corpus opt-in yet" so the
        # full-fixture sweep and ``verify_urlset_diff.py`` identity in
        # that commit were meaningful. Ulteig (C16, the motivating
        # board) is the first opt-in, landed by the follow-up
        # ``integrate-company`` commit this whitelist now records, and
        # its fixture is captured with suppression active — mandatory
        # here rather than stylistic, because UKG's featured section is
        # personalized and drifts independently of the real result set,
        # so a fixture that froze it would carry a flaky count.
        # Mirrors ``TestPaginateField.
        # test_paginate_true_corpus_entries_are_whitelisted`` — every
        # subsequent suppression opt-in MUST be added here in the same
        # commit that adds it to ``COMPANIES``.
        expected_suppression = {"Ulteig", "SentinelOne", "Find Job Latam"}
        actual_suppression = {
            c.name for c in COMPANIES if c.link_rule.suppress_ancestor_selector
        }
        assert actual_suppression == expected_suppression, (
            f"suppress_ancestor_selector corpus set drift: "
            f"unexpected={actual_suppression - expected_suppression}, "
            f"missing={expected_suppression - actual_suppression}"
        )


class TestCompaniesCatalogIntegrity:
    """Sanity checks on the shipped ``COMPANIES`` list.

    These are guardrails, not exhaustive checks — they catch the failure
    modes most likely to slip through review (duplicate slugs, empty
    required fields, drift in the count).
    """

    def test_catalog_is_non_empty(self) -> None:
        assert len(COMPANIES) > 0

    def test_all_names_are_unique(self) -> None:
        names = [c.name for c in COMPANIES]
        assert len(names) == len(set(names)), "duplicate company name in COMPANIES"

    def test_all_slugs_are_unique(self) -> None:
        # Slugs feed both output/ filenames and tests/fixtures/snapshots/
        # directory names — a collision here would silently overwrite
        # one company's snapshot with another's.
        slugs = [slugify(c.name) for c in COMPANIES]
        assert len(slugs) == len(set(slugs)), "duplicate slug in COMPANIES"

    def test_no_empty_required_fields(self) -> None:
        for c in COMPANIES:
            assert c.name.strip(), f"empty name in {c!r}"
            assert c.job_board_url.strip(), f"empty job_board_url in {c.name!r}"
            assert c.sample_job_url.strip(), f"empty sample_job_url in {c.name!r}"

    def test_urls_are_absolute(self) -> None:
        # Same-origin matching relies on both URLs being absolute; a
        # relative URL slipping in would silently break origin
        # derivation at runtime.
        for c in COMPANIES:
            assert c.job_board_url.startswith(("http://", "https://")), (
                f"non-absolute job_board_url in {c.name!r}: {c.job_board_url!r}"
            )
            assert c.sample_job_url.startswith(("http://", "https://")), (
                f"non-absolute sample_job_url in {c.name!r}: {c.sample_job_url!r}"
            )


class TestStrategyField:
    """SYS-4 additions to ``Company``: the ``strategy`` field + Greenhouse
    host validator.

    The DOM path is the default and covered by every other test in this
    file (they all instantiate ``Company`` without ``strategy=...``);
    this class focuses on the two things that only matter for the port:
    the ``Literal`` type-check on the field value, and the
    ``model_validator`` gate on the Greenhouse-strategy URL shape.
    """

    def test_default_strategy_is_dom(self) -> None:
        # Backwards compatibility with every pre-SYS-4 entry in the
        # catalog: an entry that predates the ``strategy`` field must
        # still parse to ``strategy="dom"`` without change.
        c = Company(
            name="Example",
            job_board_url="https://example.com/careers",
            sample_job_url="https://example.com/jobs/1",
        )
        assert c.strategy == "dom"

    def test_unknown_strategy_is_rejected(self) -> None:
        # The ``StrategyName`` ``Literal`` blocks unknown names at
        # schema-validation time; ``get_strategy`` never sees them.
        with pytest.raises(ValidationError):
            Company(
                name="Example",
                job_board_url="https://example.com/careers",
                sample_job_url="https://example.com/jobs/1",
                strategy="workday",  # type: ignore[arg-type]
            )

    def test_greenhouse_canonical_host_is_accepted(self) -> None:
        c = Company(
            name="Zscaler",
            job_board_url="https://job-boards.greenhouse.io/zscaler",
            sample_job_url="https://job-boards.greenhouse.io/zscaler/jobs/1",
            strategy="greenhouse",
        )
        assert c.strategy == "greenhouse"

    def test_greenhouse_legacy_host_is_accepted(self) -> None:
        # ``boards.greenhouse.io`` is the pre-2024 host; it still
        # resolves for many tenants, so we accept it alongside the
        # current ``job-boards.greenhouse.io``.
        c = Company(
            name="ExampleGH",
            job_board_url="https://boards.greenhouse.io/example",
            sample_job_url="https://boards.greenhouse.io/example/jobs/1",
            strategy="greenhouse",
        )
        assert c.strategy == "greenhouse"

    def test_greenhouse_with_marketing_url_is_rejected(self) -> None:
        # The whole reason the validator exists: a Greenhouse-hosted
        # board's token is not always derivable from the marketing
        # domain (West Monroe's token is ``westmonroe4``), so we make
        # it a schema-time error to point ``strategy="greenhouse"`` at
        # anything but a canonical board URL.
        with pytest.raises(ValidationError, match="job-boards.greenhouse.io"):
            Company(
                name="Marketing Only",
                job_board_url="https://westmonroe.com/careers",
                sample_job_url="https://westmonroe.com/careers/jobs/1",
                strategy="greenhouse",
            )

    def test_greenhouse_without_board_token_is_rejected(self) -> None:
        # Correct host, but no board token in the path: this catches
        # the copy-paste-mistake case where someone lands on a
        # Greenhouse homepage / tenant-less URL.
        with pytest.raises(ValidationError, match="board token"):
            Company(
                name="No Token",
                job_board_url="https://job-boards.greenhouse.io/",
                sample_job_url="https://job-boards.greenhouse.io/xyz/jobs/1",
                strategy="greenhouse",
            )

    def test_non_greenhouse_strategy_bypasses_host_check(self) -> None:
        # The validator must be a strict no-op for ``strategy="dom"`` --
        # otherwise every existing catalog entry (37 of 37 at SYS-4
        # Task 2) would fail its host check on import.
        c = Company(
            name="Any DOM Site",
            job_board_url="https://anysite.example.com/careers",
            sample_job_url="https://anysite.example.com/jobs/1",
        )
        assert c.strategy == "dom"


class TestPaginateField:
    """SYS-5 addition to ``Company``: the opt-in ``paginate`` flag.

    The walker itself is exercised in
    ``tests/snapshots/test_pagination_walker.py``; here we only pin the
    schema-time contract — default, storage, immutability, type gate,
    and the invariant that no corpus company opts in at SYS-5 landing
    time. Techwarely and BCG (the boards SYS-5 unblocks) are integrated
    through the standard ``integrate-company`` workflow in follow-up
    commits, at which point their entries will flip this bit.
    """

    def test_default_paginate_is_false(self) -> None:
        # A minimal Company keeps the pre-SYS-5 single-shot code path
        # by default. The walker is never loaded for these entries.
        c = Company(
            name="Example",
            job_board_url="https://example.com/careers",
            sample_job_url="https://example.com/jobs/1",
        )
        assert c.paginate is False

    def test_explicit_paginate_true_is_stored(self) -> None:
        c = Company(
            name="Example",
            job_board_url="https://example.com/careers",
            sample_job_url="https://example.com/jobs/1",
            paginate=True,
        )
        assert c.paginate is True

    def test_paginate_frozen(self) -> None:
        # ``frozen=True`` on the model — assigning to ``paginate``
        # after construction must raise, same as every other field.
        c = Company(
            name="Example",
            job_board_url="https://example.com/careers",
            sample_job_url="https://example.com/jobs/1",
            paginate=True,
        )
        with pytest.raises(ValidationError):
            c.paginate = False  # type: ignore[misc]

    def test_non_bool_paginate_is_rejected(self) -> None:
        # Pydantic 2 coerces "true"/"false" strings to bools by default;
        # we want to catch obviously-wrong values like a plain int > 1
        # so a fat-finger ``paginate=2`` fails loudly instead of being
        # silently truthy. The Company model does not carry a custom
        # coercer for this field, but pydantic's own strict-bool
        # semantics reject ``"yes"`` / arbitrary strings.
        with pytest.raises(ValidationError):
            Company(
                name="Example",
                job_board_url="https://example.com/careers",
                sample_job_url="https://example.com/jobs/1",
                paginate="not-a-bool",  # type: ignore[arg-type]
            )

    def test_paginate_true_corpus_entries_are_whitelisted(self) -> None:
        # SYS-5 shipped as opt-in; only boards whose pagination is
        # live-validated through the standard integrate-company workflow
        # should carry the flag. Techwarely (integrated 2026-07-17) is
        # the first corpus entry to flip this bit. BCG, the other SYS-5
        # live-validation board, is deliberately absent: it integrated as
        # ``strategy="phenom"`` (SYS-15), which bypasses the walker
        # entirely, so its runtime pagination never became a catalog
        # opt-in. Any
        # other corpus entry with paginate=True is a red flag — either
        # an accidental default flip in ``company.py`` or a paginated
        # company that skipped the standard workflow. Update the
        # whitelist explicitly as part of the same commit that adds a
        # new paginate=True entry to ``COMPANIES``; drive-by expansion
        # of this set is exactly what the guardrail exists to catch.
        expected_paginated = {
            "Techwarely",
            "Nearshore Business Solutions",
            "APM Terminals",
            "Svitla",
            "Dev.Pro",
            "Hire With Near",
            "Nextern",
            "Concentrix",
        }
        actual_paginated = {c.name for c in COMPANIES if c.paginate}
        assert actual_paginated == expected_paginated, (
            f"paginate=True corpus set drift: "
            f"unexpected={actual_paginated - expected_paginated}, "
            f"missing={expected_paginated - actual_paginated}"
        )


class TestExpectedJobsField:
    """SYS-9 addition to ``Company``: the human-counted ``expected_jobs`` target.

    Locks the schema-level contract for the new field. The verdict
    computation itself (and the way ``expected_jobs=None`` maps to
    ``verdict="unverified"``) lives on ``build_report`` and is covered
    in ``tests/unit/test_strategy_port.py``; here we only pin the
    Company-schema surface — default, storage, immutability, non-
    negativity — so an accidental type change to the field (or a stray
    ``None``-becomes-``0`` coercion) fails loudly at import time
    instead of silently flipping every ``unverified`` report to a
    ``match``/``over``.
    """

    def test_default_expected_jobs_is_none(self) -> None:
        # Backwards compatibility: every pre-SYS-9 catalog entry must
        # still parse to ``expected_jobs=None`` without touching the
        # entry — the SYS-9 plan explicitly forbids backfilling the 57
        # existing rows.
        c = Company(
            name="Example",
            job_board_url="https://example.com/careers",
            sample_job_url="https://example.com/jobs/1",
        )
        assert c.expected_jobs is None

    def test_positive_expected_jobs_is_stored(self) -> None:
        c = Company(
            name="Example",
            job_board_url="https://example.com/careers",
            sample_job_url="https://example.com/jobs/1",
            expected_jobs=7,
        )
        assert c.expected_jobs == 7

    def test_zero_expected_jobs_is_valid(self) -> None:
        # A Case-C board (filter exists, no options match the region)
        # has a well-defined expectation of zero. This is *not* the
        # same as ``None`` (never counted) — a 0/0 pairing is a
        # ``match`` verdict, and the schema must accept it.
        c = Company(
            name="Case C",
            job_board_url="https://example.com/careers",
            sample_job_url="https://example.com/jobs/1",
            expected_jobs=0,
        )
        assert c.expected_jobs == 0

    def test_negative_expected_jobs_is_rejected(self) -> None:
        # ``ge=0`` — a negative count is nonsense and would break the
        # verdict arithmetic (delta signs). Validated at construction
        # time so an accidental sign flip in ``new-companies.json``
        # fails loudly at catalog import, not at report render time.
        with pytest.raises(ValidationError):
            Company(
                name="Example",
                job_board_url="https://example.com/careers",
                sample_job_url="https://example.com/jobs/1",
                expected_jobs=-1,
            )

    def test_expected_jobs_frozen(self) -> None:
        c = Company(
            name="Example",
            job_board_url="https://example.com/careers",
            sample_job_url="https://example.com/jobs/1",
            expected_jobs=3,
        )
        with pytest.raises(ValidationError):
            c.expected_jobs = 4  # type: ignore[misc]

    def test_no_corpus_entry_carries_expected_jobs_yet(self) -> None:
        # SYS-9 landed with strict "no backfill of existing entries";
        # every one of the pre-SYS-9 corpus rows had to still be ``None``
        # at SYS-9 merge time. Svitla (integrated 2026-08-05) is the
        # first corpus entry to flip this to a real integer through the
        # standard ``integrate-company`` workflow, so the test tightens
        # to a whitelist mirror of ``TestPaginateField.
        # test_paginate_true_corpus_entries_are_whitelisted`` from here
        # on. Every subsequent integration that flips ``expected_jobs``
        # from ``None`` to an integer MUST add the entry to this
        # whitelist in the same commit.
        expected_counted = {
            "Svitla": 31,
            "Boston Consulting Group": 13,
            "Dev.Pro": 25,
            "Citi": 12,
            "Ulteig": 10,
            "SentinelOne": 7,
            "Find Job Latam": 2,
            "Varicent": 4,
            "Babel": 5,
            "Railway": 16,
            "Oceans Code Experts": 7,
            "TOMIA": 1,
            "Convera": 3,
            "Cloudbeds": 9,
            "REAP": 6,
            "Smarsh": 1,
            "Jobsity": 6,
            "Plan A Technologies": 3,
            "Imprivata": 1,
            "Movate": 8,
            "Hire With Near": 214,
            "Sapphire Labs": 10,
            "GreenSlate": 10,
            "CSC Generation": 7,
            "Cohesity": 2,
            "Fortinet": 3,
            "Veeam Software": 11,
            "Cornelis Networks": 7,
            "American Express Global Business Travel": 1,
            "Definity": 10,
            "AireSpring": 12,
            "Nextern": 19,
            "LSEG": 7,
            "Oliver Healthcare Packaging": 1,
            "Roche": 12,
            "Cirtec Medical": 5,
            "DXC Technology": 3,
            "Progress": 4,
            "Vintti": 51,
            "UST": 20,
            "Coloplast": 22,
            "Pythian": 3,
            "Acuity Analytics": 5,
            "Terumo Blood and Cell Technologies": 5,
            "Concentrix": 16,
            "Medtronic": 14,
        }
        actual_counted = {
            c.name: c.expected_jobs for c in COMPANIES if c.expected_jobs is not None
        }
        common = set(actual_counted) & set(expected_counted)
        value_mismatches = {
            k: (actual_counted.get(k), expected_counted.get(k))
            for k in common
            if actual_counted.get(k) != expected_counted.get(k)
        }
        assert actual_counted == expected_counted, (
            f"expected_jobs corpus set drift: "
            f"unexpected={set(actual_counted) - set(expected_counted)}, "
            f"missing={set(expected_counted) - set(actual_counted)}, "
            f"value_mismatches={value_mismatches}"
        )


class TestRuntimeHooksSchema:
    """SYS-12 addition: the nested ``RuntimeHooks`` sub-model.

    These tests pin the schema-level contract on the hooks bag itself
    — defaults, immutability, extra-field rejection, and the
    ``is_inert`` property that downstream call sites gate on. The
    cross-field validators live on ``Company`` and are covered by
    :class:`TestHooksField` below.
    """

    def test_defaults_are_inert(self) -> None:
        # The whole point of the default: every field maps to a "no-op"
        # value so an entry that does not opt in is byte-identical to
        # the pre-SYS-12 code paths.
        h = RuntimeHooks()
        assert h.expand_selector is None
        assert h.next_control_selector is None
        assert h.filter_already_applied is False
        assert h.pre_extract_css is None
        assert h.is_inert is True

    def test_is_inert_false_when_any_field_set(self) -> None:
        # is_inert is the single downstream gating check — one non-default
        # field is enough to flip it, on every field independently.
        assert RuntimeHooks(expand_selector=".x").is_inert is False
        assert (
            RuntimeHooks(
                next_control_selector=".n", filter_already_applied=False
            ).is_inert
            is False
        )
        assert RuntimeHooks(filter_already_applied=True).is_inert is False
        assert RuntimeHooks(pre_extract_css=".a{display:block}").is_inert is False

    def test_is_inert_not_in_model_dump(self) -> None:
        # is_inert is a plain @property, not a @computed_field: it must
        # not leak into serialization or model_dump / model_dump_json.
        # If someone ever "helpfully" upgrades it to @computed_field this
        # test fails, forcing the discussion.
        dumped = RuntimeHooks().model_dump()
        assert "is_inert" not in dumped
        assert set(dumped) == {
            "expand_selector",
            "next_control_selector",
            "filter_already_applied",
            "pre_extract_css",
        }

    def test_frozen(self) -> None:
        h = RuntimeHooks()
        with pytest.raises(ValidationError):
            h.expand_selector = ".x"  # type: ignore[misc]

    def test_extra_field_forbidden(self) -> None:
        # ``extra="forbid"`` — a typo like ``expand_selectors`` (plural)
        # in a catalog entry fails loudly at import instead of being
        # silently ignored and leaving the runtime hook effectively
        # unset.
        with pytest.raises(ValidationError):
            RuntimeHooks(expand_selectors=".x")  # type: ignore[call-arg]

    def test_two_default_instances_compare_equal(self) -> None:
        # ``is_inert`` is implemented as equality with ``RuntimeHooks()``,
        # which relies on BaseModel's field-based __eq__. Pin that
        # semantics explicitly so a future model_config change (e.g.
        # ``eq=False``) does not silently break every hook gate.
        assert RuntimeHooks() == RuntimeHooks()


class TestHooksField:
    """SYS-12 addition on ``Company``: the ``hooks`` field + its two
    cross-field validators.

    The schema-time contract: any non-inert hooks require
    ``strategy="dom"`` (the Greenhouse API path bypasses the
    deterministic hook phase entirely), and ``next_control_selector``
    requires ``paginate=True`` (the override is only meaningful inside
    the walker). Both fire at catalog-import time so a misconfigured
    entry never reaches runtime.
    """

    def test_default_hooks_are_inert(self) -> None:
        c = Company(
            name="Example",
            job_board_url="https://example.com/careers",
            sample_job_url="https://example.com/jobs/1",
        )
        assert c.hooks == RuntimeHooks()
        assert c.hooks.is_inert is True

    def test_explicit_non_inert_hooks_are_stored(self) -> None:
        hooks = RuntimeHooks(
            expand_selector=".acc", pre_extract_css=".x{display:block}"
        )
        c = Company(
            name="Example",
            job_board_url="https://example.com/careers",
            sample_job_url="https://example.com/jobs/1",
            hooks=hooks,
        )
        assert c.hooks == hooks
        assert c.hooks.is_inert is False

    def test_hooks_frozen_on_company(self) -> None:
        c = Company(
            name="Example",
            job_board_url="https://example.com/careers",
            sample_job_url="https://example.com/jobs/1",
        )
        with pytest.raises(ValidationError):
            c.hooks = RuntimeHooks(expand_selector=".x")  # type: ignore[misc]

    def test_non_inert_hooks_on_greenhouse_strategy_rejected(self) -> None:
        # Deterministic hooks are executed by ``extraction.dom.collector``;
        # the Greenhouse API path never renders a page, so any hook set
        # would be silently ignored. The validator makes that a schema
        # error, and the error message names the offending fields so
        # the fix is obvious.
        with pytest.raises(ValidationError, match="strategy='dom'"):
            Company(
                name="GH With Hooks",
                job_board_url="https://job-boards.greenhouse.io/example",
                sample_job_url="https://job-boards.greenhouse.io/example/jobs/1",
                strategy="greenhouse",
                hooks=RuntimeHooks(pre_extract_css=".x{display:block}"),
            )

    def test_filter_already_applied_alone_on_greenhouse_rejected(self) -> None:
        # ``filter_already_applied`` is prompt-side (an agent-only clause),
        # so it does not fire on the Greenhouse path either — but the
        # validator's rule is uniform "any non-inert field", not "any
        # collector-side field". Pin the uniform behaviour so the rule
        # remains a single mental model rather than a per-field carveout.
        with pytest.raises(ValidationError, match="filter_already_applied"):
            Company(
                name="GH FA",
                job_board_url="https://job-boards.greenhouse.io/example",
                sample_job_url="https://job-boards.greenhouse.io/example/jobs/1",
                strategy="greenhouse",
                hooks=RuntimeHooks(filter_already_applied=True),
            )

    def test_non_inert_hooks_on_dom_strategy_accepted(self) -> None:
        # The happy path: strategy="dom" (the default) plus any hook
        # combination is legal at the schema level; runtime behaviour
        # is a separate concern covered by the collector tests.
        c = Company(
            name="Dom With Hooks",
            job_board_url="https://example.com/careers",
            sample_job_url="https://example.com/jobs/1",
            hooks=RuntimeHooks(
                expand_selector=".acc",
                pre_extract_css=".x{display:block}",
                filter_already_applied=True,
            ),
        )
        assert c.hooks.is_inert is False
        assert c.strategy == "dom"

    def test_next_control_selector_without_paginate_rejected(self) -> None:
        # The walker is the only consumer of the override; single-shot
        # ``paginate=False`` companies would silently ignore it.
        with pytest.raises(ValidationError, match="paginate=True"):
            Company(
                name="Bad Override",
                job_board_url="https://example.com/careers",
                sample_job_url="https://example.com/jobs/1",
                hooks=RuntimeHooks(next_control_selector="button.next"),
            )

    def test_next_control_selector_with_paginate_accepted(self) -> None:
        c = Company(
            name="Good Override",
            job_board_url="https://example.com/careers",
            sample_job_url="https://example.com/jobs/1",
            paginate=True,
            hooks=RuntimeHooks(next_control_selector="button.next"),
        )
        assert c.hooks.next_control_selector == "button.next"

    def test_corpus_entries_have_inert_hooks(self) -> None:
        # SYS-12 landed with strict "no corpus opt-in yet"; every
        # pre-SYS-12 catalog entry carried the inert default. Svitla
        # (integrated 2026-08-05) is the first corpus entry to opt in
        # to a non-inert hook (``filter_already_applied=True`` guarding
        # the ``?country=10`` URL pre-filter against agent re-toggle),
        # so the test tightens to a whitelist mirror of
        # ``TestPaginateField.test_paginate_true_corpus_entries_are_whitelisted``
        # from here on. Every subsequent integration that ships a
        # non-inert ``hooks`` bag MUST add the entry to this whitelist
        # in the same commit.
        expected_non_inert = {
            "Svitla",
            "Ulteig",
            "SentinelOne",
            "Find Job Latam",
            "TOMIA",
            "Convera",
            "Smarsh",
            "Jobsity",
            "Movate",
            "Hire With Near",
            "Cohesity",
            "Fortinet",
            "American Express Global Business Travel",
            "Nextern",
            "Cirtec Medical",
            "DXC Technology",
            "Coloplast",
            "Pythian",
            "Terumo Blood and Cell Technologies",
        }
        actual_non_inert = {c.name for c in COMPANIES if not c.hooks.is_inert}
        assert actual_non_inert == expected_non_inert, (
            f"non-inert hooks corpus set drift: "
            f"unexpected={actual_non_inert - expected_non_inert}, "
            f"missing={expected_non_inert - actual_non_inert}"
        )


class TestPreFilterUrlsField:
    """SYS-13 addition on ``Company``: the ``pre_filter_urls`` tuple + its
    two cross-field validators.

    The schema-time contract: non-empty ``pre_filter_urls`` requires
    ``strategy="dom"`` (the agent-less multi-state runner lives in
    :class:`DomStrategy`; the Greenhouse API path never navigates URLs),
    and every declared URL shares origin with ``job_board_url`` via
    scheme+netloc equality (which also rejects relative URLs for free).
    Both fire at catalog-import time so a misconfigured entry never
    reaches runtime. The runner itself is covered by
    ``tests/unit/test_prefiltered.py``; here we only pin the
    Company-schema surface.
    """

    def test_default_pre_filter_urls_is_empty_tuple(self) -> None:
        # Backwards compatibility: every pre-SYS-13 catalog entry must
        # still parse to ``pre_filter_urls=()`` without touching the
        # entry — the empty tuple is the sentinel that keeps
        # ``DomStrategy.extract`` on the pre-SYS-13 agent path.
        c = Company(
            name="Example",
            job_board_url="https://example.com/careers",
            sample_job_url="https://example.com/jobs/1",
        )
        assert c.pre_filter_urls == ()

    def test_explicit_pre_filter_urls_are_stored(self) -> None:
        urls = (
            "https://example.com/careers?location_id=1",
            "https://example.com/careers?location_id=2",
        )
        c = Company(
            name="Example",
            job_board_url="https://example.com/careers",
            sample_job_url="https://example.com/jobs/1",
            pre_filter_urls=urls,
        )
        assert c.pre_filter_urls == urls

    def test_pre_filter_urls_frozen(self) -> None:
        # ``frozen=True`` on the model — assigning to
        # ``pre_filter_urls`` after construction must raise.
        c = Company(
            name="Example",
            job_board_url="https://example.com/careers",
            sample_job_url="https://example.com/jobs/1",
        )
        with pytest.raises(ValidationError):
            c.pre_filter_urls = (  # type: ignore[misc]
                "https://example.com/careers?x=1",
            )

    def test_pre_filter_urls_is_tuple_not_list(self) -> None:
        # ``tuple`` (frozen, hashable) matches the ``aliases`` field
        # convention. Pydantic coerces list → tuple on the way in;
        # pin the observed type so a future ``list[str]`` annotation
        # flip (which would silently break hashability of the Company
        # instance) fails loudly.
        c = Company(
            name="Example",
            job_board_url="https://example.com/careers",
            sample_job_url="https://example.com/jobs/1",
            pre_filter_urls=["https://example.com/careers?x=1"],  # type: ignore[arg-type]
        )
        assert isinstance(c.pre_filter_urls, tuple)

    def test_non_empty_pre_filter_urls_on_greenhouse_rejected(self) -> None:
        # The agent-less multi-state runner is a DomStrategy path; the
        # Greenhouse API path never navigates URLs, so any declared
        # states would be silently ignored. Schema-time error, error
        # message names both the strategy and the offending URLs.
        with pytest.raises(ValidationError, match="strategy='dom'"):
            Company(
                name="GH With States",
                job_board_url="https://job-boards.greenhouse.io/example",
                sample_job_url="https://job-boards.greenhouse.io/example/jobs/1",
                strategy="greenhouse",
                pre_filter_urls=(
                    "https://job-boards.greenhouse.io/example?location=cr",
                ),
            )

    def test_same_origin_query_string_variant_accepted(self) -> None:
        # The Plan A (C18) exemplar shape: identical path, distinct
        # query strings. This is the primary use case.
        c = Company(
            name="Plan A Shape",
            job_board_url="https://tenant.peopleforce.io/careers",
            sample_job_url="https://tenant.peopleforce.io/careers/v/1",
            pre_filter_urls=(
                "https://tenant.peopleforce.io/careers?location_id=1",
                "https://tenant.peopleforce.io/careers?location_id=2",
            ),
        )
        assert len(c.pre_filter_urls) == 2

    def test_same_origin_path_variant_accepted(self) -> None:
        # Same origin, different path: also legal. Some boards route
        # per-region via path segments rather than query strings, and
        # the validator is intentionally origin-only (not path-only).
        c = Company(
            name="Path Variant",
            job_board_url="https://example.com/careers",
            sample_job_url="https://example.com/jobs/1",
            pre_filter_urls=(
                "https://example.com/careers/cartago",
                "https://example.com/careers/heredia",
            ),
        )
        assert len(c.pre_filter_urls) == 2

    def test_cross_origin_different_host_rejected(self) -> None:
        # Different host is the classic misconfiguration — a state URL
        # pointing at a marketing subdomain or a different tenant.
        # Same-origin invariant across the DOM matcher, base-href
        # resolution, and snapshot top_url makes this a schema error.
        with pytest.raises(ValidationError, match="share origin"):
            Company(
                name="Cross Host",
                job_board_url="https://example.com/careers",
                sample_job_url="https://example.com/jobs/1",
                pre_filter_urls=(
                    "https://example.com/careers?x=1",
                    "https://other.example.com/careers?x=1",
                ),
            )

    def test_cross_origin_different_scheme_rejected(self) -> None:
        # Scheme mismatch — http vs https — is also a same-origin
        # violation (netloc-only equality would let this through, so
        # we compare the full ``(scheme, netloc)`` tuple).
        with pytest.raises(ValidationError, match="share origin"):
            Company(
                name="Cross Scheme",
                job_board_url="https://example.com/careers",
                sample_job_url="https://example.com/jobs/1",
                pre_filter_urls=("http://example.com/careers?x=1",),
            )

    def test_relative_url_rejected(self) -> None:
        # ``urlparse`` on a relative URL yields empty scheme and
        # netloc; the origin tuple compares unequal against any
        # absolute ``job_board_url``, and the error surfaces at
        # catalog-import time rather than at ``navigate_to`` time
        # where base-href resolution would fail cryptically.
        with pytest.raises(ValidationError, match="share origin"):
            Company(
                name="Relative",
                job_board_url="https://example.com/careers",
                sample_job_url="https://example.com/jobs/1",
                pre_filter_urls=("/careers?location_id=1",),
            )

    def test_filter_already_applied_alongside_pre_filter_urls_accepted(
        self,
    ) -> None:
        # The plan (§4.6) is explicit: ``filter_already_applied``
        # alongside ``pre_filter_urls`` is *not* rejected — the flag
        # is prompt-side only and is simply never read on the
        # agent-less path. Pin this so a future "helpful" validator
        # addition doesn't quietly turn it into an error.
        c = Company(
            name="Redundant Flag",
            job_board_url="https://example.com/careers",
            sample_job_url="https://example.com/jobs/1",
            hooks=RuntimeHooks(filter_already_applied=True),
            pre_filter_urls=("https://example.com/careers?x=1",),
        )
        assert c.hooks.filter_already_applied is True
        assert len(c.pre_filter_urls) == 1

    def test_pre_filter_urls_corpus_entries_are_whitelisted(self) -> None:
        # doola declares a single URL, which is a different use of the
        # field than the multi-city union it was built for. It is here
        # because the agent cannot be trusted with this board: Lever
        # renders a visible location filter bar, and the agent
        # interacts with it even under ``filter_already_applied``,
        # producing 8 / 1 / 1 without the hook and 1 / 6 / 4 with it.
        # The agent-less path removes the LLM from the loop entirely,
        # so the filter that ships is exactly the one in the URL.
        #
        # Plan A Technologies was the field's first opt-in, declaring
        # one ``?location_id=<n>`` URL per Costa Rica city. That worked
        # but could only ever be correct on the day it was written: the
        # declared set is frozen while the tenant's location list is
        # not, so a posting opened in an undeclared city is missed —
        # and missed *silently*, because the union still equals the
        # frozen ``expected_jobs`` and the verdict stays ``match``.
        # It now runs on ``strategy="peopleforce"``, which discovers
        # the region's locations from the board on every run.
        #
        # Those two cases mark the field's boundary. A frozen URL list
        # is right where the *filter values* cannot change without a
        # human noticing (doola: one stable option) and wrong where
        # they can (Plan A: a tenant-editable city list). Every opt-in
        # MUST be added here in the same commit that adds it to
        # ``COMPANIES``.
        # Sapphire Labs is the doola case again, with a different
        # trigger: its #location-select is a decoy offering only "All"
        # and the typo'd "Headquater", so the agent reads a real region
        # filter, matches nothing, and fires Case C for 0 jobs — with
        # or without ``filter_already_applied``. The declared URL is
        # the bare board (no filter parameter at all), because the
        # board needs no filtering: all 10 postings are San Salvador /
        # Fully Remote. It sits on the safe side of the boundary above
        # — there are no filter values to drift, so the frozen URL
        # cannot silently go stale the way Plan A's city list could.
        # CSC Generation is doola's case on the same platform, measured:
        # under ``filter_already_applied`` three live runs produced
        # 7 / 465 / 7 — the one failure being the agent clearing Lever's
        # location filter bar and exposing the entire board. It is on
        # the safe side of the boundary above: "Costa Rica" is a single
        # stable option and the only region-matching value on the
        # board's facet, so there is no editable city list to drift.
        expected_states: set[str] = {
            "doola",
            "REAP",
            "Sapphire Labs",
            "CSC Generation",
            "AireSpring",
            "LSEG",
            "Oliver Healthcare Packaging",
            "Progress",
            "Vintti",
            "Concentrix",
            "Medtronic",
        }
        actual_states = {c.name for c in COMPANIES if c.pre_filter_urls}
        assert actual_states == expected_states, (
            f"pre_filter_urls corpus set drift: "
            f"unexpected={actual_states - expected_states}, "
            f"missing={expected_states - actual_states}"
        )


class TestPhenomConfigSchema:
    """SYS-15: the ``PhenomConfig`` model itself."""

    def test_page_id_is_required(self) -> None:
        # There is no way to derive a tenant's Phenom page id from its
        # board URL, so it has no default — omitting it must fail at
        # construction rather than produce a request body the API
        # silently answers with the wrong tenant's jobs.
        with pytest.raises(ValidationError):
            PhenomConfig()  # type: ignore[call-arg]

    def test_defaults_match_the_observed_bcg_request(self) -> None:
        # Both defaults are the values BCG's live widget issues; they
        # are defaults rather than required fields because every Phenom
        # tenant observed so far agrees on them. A tenant that differs
        # overrides explicitly.
        cfg = PhenomConfig(page_id="page17-ds")
        assert cfg.locale == "en_global"
        assert cfg.endpoint_path == "/widgets"

    def test_frozen(self) -> None:
        cfg = PhenomConfig(page_id="page17-ds")
        with pytest.raises(ValidationError):
            cfg.page_id = "other"  # type: ignore[misc]

    def test_extra_field_forbidden(self) -> None:
        # The remaining request-body fields are not tenant-specific and
        # live in the adapter's body builder. Adding one here would
        # split the wire contract across two files.
        with pytest.raises(ValidationError):
            PhenomConfig(page_id="page17-ds", size=100)  # type: ignore[call-arg]


class TestPhenomStrategyPresenceValidator:
    """SYS-15: ``strategy="phenom"`` ⇔ ``phenom`` config, both directions.

    Presence is the *entire* schema-time gate for this strategy. Phenom
    is tenant-co-hosted — BCG's widget answers on ``careers.bcg.com``,
    the company's own marketing domain — so unlike Greenhouse there is
    no platform hostname to allow-list. That absence is deliberate and
    is pinned by ``test_marketing_host_is_accepted`` below; a future
    contributor adding a host validator here would break every Phenom
    tenant, since each one is on a different domain.
    """

    def _phenom_company(self, **overrides: object) -> Company:
        defaults: dict[str, object] = {
            "name": "BCG",
            "job_board_url": "https://careers.bcg.com/global/en/search-results",
            "sample_job_url": "https://careers.bcg.com/global/en/job/58329/A-Title",
            "strategy": "phenom",
            "phenom": PhenomConfig(page_id="page17-ds"),
            "link_rule": LinkRule(path_prefix="/global/en/job"),
        }
        defaults.update(overrides)
        return Company(**defaults)  # type: ignore[arg-type]

    def test_phenom_strategy_with_config_is_accepted(self) -> None:
        c = self._phenom_company()
        assert c.strategy == "phenom"
        assert c.phenom is not None
        assert c.phenom.page_id == "page17-ds"

    def test_marketing_host_is_accepted(self) -> None:
        # The no-host-validator pin. ``careers.bcg.com`` is BCG's own
        # domain, not a platform host; the Greenhouse-style host
        # allow-list would reject it and must never be added here.
        c = self._phenom_company()
        assert "careers.bcg.com" in c.job_board_url

    def test_phenom_strategy_without_config_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="requires a phenom="):
            Company(
                name="BCG No Config",
                job_board_url="https://careers.bcg.com/global/en/search-results",
                sample_job_url="https://careers.bcg.com/global/en/job/1/T",
                strategy="phenom",
            )

    def test_config_on_dom_strategy_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="strategy='dom'"):
            Company(
                name="Dom With Phenom Config",
                job_board_url="https://example.com/careers",
                sample_job_url="https://example.com/jobs/1",
                phenom=PhenomConfig(page_id="page17-ds"),
            )

    def test_config_on_greenhouse_strategy_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="strategy='greenhouse'"):
            Company(
                name="GH With Phenom Config",
                job_board_url="https://job-boards.greenhouse.io/example",
                sample_job_url="https://job-boards.greenhouse.io/example/jobs/1",
                strategy="greenhouse",
                phenom=PhenomConfig(page_id="page17-ds"),
            )

    def test_default_phenom_is_none(self) -> None:
        c = Company(
            name="Example",
            job_board_url="https://example.com/careers",
            sample_job_url="https://example.com/jobs/1",
        )
        assert c.phenom is None

    def test_phenom_strategy_corpus_entries_are_whitelisted(self) -> None:
        # SYS-15 landed with no corpus opt-in at all; BCG — the board
        # that motivated the adapter — was integrated by the follow-up
        # ``integrate-company`` commit this whitelist now records. Its
        # regression artifact is the recorded API payload at
        # ``tests/fixtures/api/phenom/bcg.json`` plus the respx-mocked
        # cases below, deliberately *not* a DOM snapshot: that
        # substitution is the whole of the C12 closure, so a
        # ``strategy="phenom"`` entry having no
        # ``tests/fixtures/snapshots/<slug>/`` directory is correct
        # rather than an omission. Mirrors
        # ``TestPaginateField.test_paginate_true_corpus_entries_are_whitelisted``
        # — every subsequent Phenom tenant MUST be added here in the
        # same commit that adds it to ``COMPANIES``.
        expected_phenom = {"Boston Consulting Group", "Roche"}
        actual_phenom = {c.name for c in COMPANIES if c.strategy == "phenom"}
        assert actual_phenom == expected_phenom, (
            f"strategy='phenom' corpus set drift: "
            f"unexpected={actual_phenom - expected_phenom}, "
            f"missing={expected_phenom - actual_phenom}"
        )


class TestTalentbrewConfigSchema:
    """SYS-17: the ``TalentbrewConfig`` model itself.

    Structurally mirrors :class:`TestPhenomConfigSchema` — both configs
    are tenant-parameter carriers guarded by ``frozen=True`` and
    ``extra="forbid"``, and both intentionally expose only the fields
    an integrator cannot reasonably discover without an evidence
    capture. The remaining request parameters live in the adapter's
    query builder, not the schema.
    """

    def test_facet_id_is_required(self) -> None:
        # ``facet_id`` is the tenant's opaque region identifier and
        # cannot be derived from ``job_board_url`` — omitting it must
        # fail at construction rather than send a facet-less request
        # the endpoint would answer with the *unfiltered* board.
        with pytest.raises(ValidationError):
            TalentbrewConfig(facet_display="Costa Rica")  # type: ignore[call-arg]

    def test_facet_display_is_required(self) -> None:
        # ``facet_display`` is kept required (not defaulted to the
        # canonical region name) so a copy-paste mismatch between
        # tenants trips validation instead of shipping a request the
        # endpoint answers but the regression tests cannot pin.
        with pytest.raises(ValidationError):
            TalentbrewConfig(facet_id="3624060")  # type: ignore[call-arg]

    def test_defaults_match_the_observed_citi_request(self) -> None:
        # ``records_per_page=15`` and ``results_path="/search-jobs/results"``
        # are the values Citi's live widget issues; they are defaults
        # rather than required fields because every Talentbrew tenant
        # observed so far agrees on them. A tenant that differs
        # overrides explicitly.
        cfg = TalentbrewConfig(facet_id="3624060", facet_display="Costa Rica")
        assert cfg.records_per_page == 15
        assert cfg.results_path == "/search-jobs/results"

    def test_records_per_page_ge_1(self) -> None:
        # Zero would divide-by-zero the walker's continue-paginating
        # threshold and negative values are nonsensical for a page
        # size, so the ``ge=1`` constraint catches both at construction.
        with pytest.raises(ValidationError):
            TalentbrewConfig(
                facet_id="3624060", facet_display="Costa Rica", records_per_page=0
            )

    def test_frozen(self) -> None:
        cfg = TalentbrewConfig(facet_id="3624060", facet_display="Costa Rica")
        with pytest.raises(ValidationError):
            cfg.facet_id = "other"  # type: ignore[misc]

    def test_extra_field_forbidden(self) -> None:
        # The remaining query-string parameters (SearchType,
        # SortCriteria, etc.) are not tenant-specific and live in the
        # adapter's query builder. Adding one here would split the
        # wire contract across two files — matching the PhenomConfig
        # discipline.
        with pytest.raises(ValidationError):
            TalentbrewConfig(  # type: ignore[call-arg]
                facet_id="3624060",
                facet_display="Costa Rica",
                search_type=5,
            )


class TestTalentbrewStrategyPresenceValidator:
    """SYS-17: ``strategy="talentbrew"`` ⇔ ``talentbrew`` config.

    Presence is the *entire* schema-time gate for this strategy.
    Talentbrew (a.k.a. Radancy) is a self-hosted enterprise ATS —
    Citi's widget answers on ``jobs.citi.com``, the company's own
    domain — so unlike Greenhouse there is no platform hostname to
    allow-list. That absence is deliberate and is pinned by
    ``test_self_hosted_host_is_accepted`` below; a future contributor
    adding a host validator here would break every Talentbrew tenant,
    since each one is on a different domain. Mirrors the
    :class:`TestPhenomStrategyPresenceValidator` shape verbatim.
    """

    def _talentbrew_company(self, **overrides: object) -> Company:
        defaults: dict[str, object] = {
            "name": "Citi",
            "job_board_url": "https://jobs.citi.com/search-jobs",
            "sample_job_url": (
                "https://jobs.citi.com/job/heredia/example-role/287/12345"
            ),
            "strategy": "talentbrew",
            "talentbrew": TalentbrewConfig(
                facet_id="3624060", facet_display="Costa Rica"
            ),
            "link_rule": LinkRule(path_prefix="/job"),
        }
        defaults.update(overrides)
        return Company(**defaults)  # type: ignore[arg-type]

    def test_talentbrew_strategy_with_config_is_accepted(self) -> None:
        c = self._talentbrew_company()
        assert c.strategy == "talentbrew"
        assert c.talentbrew is not None
        assert c.talentbrew.facet_id == "3624060"
        assert c.talentbrew.facet_display == "Costa Rica"

    def test_self_hosted_host_is_accepted(self) -> None:
        # The no-host-validator pin. ``jobs.citi.com`` is Citi's own
        # subdomain, not a platform host; the Greenhouse-style host
        # allow-list would reject it and must never be added here.
        c = self._talentbrew_company()
        assert "jobs.citi.com" in c.job_board_url

    def test_talentbrew_strategy_without_config_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="requires a talentbrew="):
            Company(
                name="Citi No Config",
                job_board_url="https://jobs.citi.com/search-jobs",
                sample_job_url="https://jobs.citi.com/job/heredia/role/287/1",
                strategy="talentbrew",
            )

    def test_config_on_dom_strategy_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="strategy='dom'"):
            Company(
                name="Dom With Talentbrew Config",
                job_board_url="https://example.com/careers",
                sample_job_url="https://example.com/jobs/1",
                talentbrew=TalentbrewConfig(
                    facet_id="3624060", facet_display="Costa Rica"
                ),
            )

    def test_config_on_greenhouse_strategy_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="strategy='greenhouse'"):
            Company(
                name="GH With Talentbrew Config",
                job_board_url="https://job-boards.greenhouse.io/example",
                sample_job_url="https://job-boards.greenhouse.io/example/jobs/1",
                strategy="greenhouse",
                talentbrew=TalentbrewConfig(
                    facet_id="3624060", facet_display="Costa Rica"
                ),
            )

    def test_config_on_phenom_strategy_is_rejected(self) -> None:
        # Cross-adapter symmetry: a talentbrew config on a
        # ``strategy="phenom"`` entry must be rejected, exactly as the
        # phenom-suite's mirror test pins the reverse direction.
        with pytest.raises(ValidationError, match="strategy='phenom'"):
            Company(
                name="Phenom With Talentbrew Config",
                job_board_url="https://careers.bcg.com/global/en/search-results",
                sample_job_url="https://careers.bcg.com/global/en/job/1/T",
                strategy="phenom",
                phenom=PhenomConfig(page_id="page17-ds"),
                link_rule=LinkRule(path_prefix="/global/en/job"),
                talentbrew=TalentbrewConfig(
                    facet_id="3624060", facet_display="Costa Rica"
                ),
            )

    def test_default_talentbrew_is_none(self) -> None:
        c = Company(
            name="Example",
            job_board_url="https://example.com/careers",
            sample_job_url="https://example.com/jobs/1",
        )
        assert c.talentbrew is None

    def test_talentbrew_strategy_corpus_entries_are_whitelisted(self) -> None:
        # SYS-17 landed with no corpus opt-in; Citi — the board that
        # motivated the adapter — was integrated by the follow-up
        # ``integrate-company`` commit this whitelist now records. Its
        # regression artifact is the recorded API payload at
        # ``tests/fixtures/api/talentbrew/citi.json`` plus the
        # respx-mocked cases in ``tests/unit/test_talentbrew.py``,
        # deliberately *not* a DOM snapshot — that substitution is
        # Citi's C12 closure, so the absence of a
        # ``tests/fixtures/snapshots/citi/`` directory is correct
        # rather than an omission. Mirrors the SYS-15 landing
        # discipline pinned by
        # ``test_phenom_strategy_corpus_entries_are_whitelisted`` —
        # every subsequent Talentbrew tenant MUST be added here in the
        # same commit that adds it to ``COMPANIES``.
        # Veeam Software is the second tenant, with the same artifact
        # shape: ``tests/fixtures/api/talentbrew/veeam.json`` plus the
        # ``TestVeeamRecordedPayload`` cases, and no DOM snapshot.
        expected_talentbrew = {"Citi", "Veeam Software"}
        actual_talentbrew = {c.name for c in COMPANIES if c.strategy == "talentbrew"}
        assert actual_talentbrew == expected_talentbrew, (
            f"strategy='talentbrew' corpus set drift: "
            f"unexpected={actual_talentbrew - expected_talentbrew}, "
            f"missing={expected_talentbrew - actual_talentbrew}"
        )

    def test_peopleforce_strategy_corpus_entries_are_whitelisted(self) -> None:
        # Plan A Technologies is the first tenant on this adapter. Its
        # regression artifact is the recorded board + filtered pages
        # under ``tests/fixtures/api/peopleforce/`` plus the
        # respx-mocked cases in ``tests/unit/test_peopleforce.py``, so
        # the absence of a ``tests/fixtures/snapshots/`` directory is
        # correct rather than an omission — the board is fetched over
        # plain HTTP and never rendered.
        #
        # Unlike its sibling adapters this strategy takes no per-tenant
        # config: PeopleForce hosts every tenant on its own subdomain,
        # so the host validator is the schema-time gate and the
        # region's location ids are discovered per run. Adding a tenant
        # is therefore a two-line catalog entry plus this whitelist, in
        # the same commit.
        expected_peopleforce = {"Plan A Technologies"}
        actual_peopleforce = {c.name for c in COMPANIES if c.strategy == "peopleforce"}
        assert actual_peopleforce == expected_peopleforce, (
            f"strategy='peopleforce' corpus set drift: "
            f"unexpected={actual_peopleforce - expected_peopleforce}, "
            f"missing={expected_peopleforce - actual_peopleforce}"
        )

    def test_bamboohr_strategy_corpus_entries_are_whitelisted(self) -> None:
        # Cornelis Networks is the first tenant on this adapter, whose
        # regression artifact is the recorded listing at
        # ``tests/fixtures/api/bamboohr/cornelisnetworks.json`` plus the
        # respx-mocked cases in ``tests/unit/test_bamboohr.py``.
        #
        # This adapter is the odd one out in the family: every sibling
        # exists because its board is unreachable or unfreezable on the
        # DOM path, whereas BambooHR's rendered board is matcher-clean
        # and two corpus entries still collect from it on
        # ``strategy="dom"``. It exists because the rendered board has
        # no region *discriminator* — no location filter, and
        # ``/careers/<id>`` anchors carrying no location — so the DOM
        # path can only return the whole board. Cornelis is 32 postings
        # of which 7 are Costa Rica.
        #
        # Consequence worth stating: a BambooHR entry on
        # ``strategy="dom"`` is not automatically wrong. It is right
        # whenever the whole board is in-region and wrong whenever it
        # is not, so migrating Gorilla Logic and Chainstack is a
        # per-board judgement, not a mechanical sweep.
        expected_bamboohr = {"Cornelis Networks"}
        actual_bamboohr = {c.name for c in COMPANIES if c.strategy == "bamboohr"}
        assert actual_bamboohr == expected_bamboohr, (
            f"strategy='bamboohr' corpus set drift: "
            f"unexpected={actual_bamboohr - expected_bamboohr}, "
            f"missing={expected_bamboohr - actual_bamboohr}"
        )


class TestCoveoConfigSchema:
    """SYS-18: the ``CoveoConfig`` model itself.

    Third in the tenant-parameter-carrier family after
    :class:`TestPhenomConfigSchema` and
    :class:`TestTalentbrewConfigSchema`. Unlike Talentbrew's
    ``records_per_page`` / ``results_path`` (platform defaults every
    observed tenant agrees on), no Coveo field has a defensible
    default — ``organization_id`` doubles as the search host's
    leftmost label, ``search_hub`` silently selects a different
    server-side query pipeline when wrong, and both token sources are
    tenant-owned with documented per-tenant variance.

    SYS-19 changed the shape: ``organization_id`` and ``search_hub``
    stay unconditionally required, while the token source became a
    two-member union — exactly one of ``token_url`` (mint over HTTP)
    or ``browser_token_key`` (borrow the page-minted JWT out of
    ``sessionStorage``, for tenants behind bot management). Making
    ``token_url`` optional was safe because ``strategy="coveo"`` had
    no corpus consumer at the time; the union validator is what keeps
    the config from carrying a silently-unread field.
    """

    def test_organization_id_is_required(self) -> None:
        # It is both the ``organizationId`` query parameter *and* the
        # search host's leftmost label, so a missing value cannot even
        # address the endpoint.
        with pytest.raises(ValidationError):
            CoveoConfig(  # type: ignore[call-arg]
                search_hub="prod-jobs-search-hub",
                token_url="https://www.ust.com/services/search",
            )

    def test_search_hub_is_required(self) -> None:
        # A wrong ``searchHub`` returns a *different result set* rather
        # than an error — the failure is silent, which is exactly why
        # this is required rather than defaulted.
        with pytest.raises(ValidationError):
            CoveoConfig(  # type: ignore[call-arg]
                organization_id="ustglobalproduction4ggrtx7v",
                token_url="https://www.ust.com/services/search",
            )

    def test_a_token_source_is_required(self) -> None:
        # SYS-19: neither source set is rejected at import rather than
        # at the first live run — the adapter cannot authenticate at
        # all without one. (Before SYS-19 this asserted that
        # ``token_url`` specifically was required; the union replaced
        # that, and the raise is now on the union validator.)
        with pytest.raises(ValidationError, match="requires a token source"):
            CoveoConfig(
                organization_id="ustglobalproduction4ggrtx7v",
                search_hub="prod-jobs-search-hub",
            )

    def test_both_token_sources_is_rejected(self) -> None:
        # The adapter reads whichever is set and would silently ignore
        # the other, so a config carrying both ships with one source
        # dead and no signal saying which — the same "unread config
        # fails loudly" rule the strategy/config validators enforce.
        with pytest.raises(ValidationError, match="exactly one token source"):
            CoveoConfig(
                organization_id="ustglobalproduction4ggrtx7v",
                search_hub="prod-jobs-search-hub",
                token_url="https://www.ust.com/services/search",
                browser_token_key="searchToken_en_us",
            )

    def test_browser_token_key_alone_is_accepted(self) -> None:
        # UST's shipped shape.
        cfg = CoveoConfig(
            organization_id="ustglobalproduction4ggrtx7v",
            search_hub="prod-jobs-search-hub",
            browser_token_key="searchToken_en_us",
        )
        assert cfg.browser_token_key == "searchToken_en_us"
        assert cfg.token_url is None

    def test_token_url_alone_is_accepted(self) -> None:
        # The pre-SYS-19 shape stays valid for ungated tenants.
        cfg = CoveoConfig(
            organization_id="ustglobalproduction4ggrtx7v",
            search_hub="prod-jobs-search-hub",
            token_url="https://www.ust.com/services/search",
        )
        assert cfg.token_url == "https://www.ust.com/services/search"
        assert cfg.browser_token_key is None

    def test_ust_values_construct(self) -> None:
        cfg = CoveoConfig(
            organization_id="ustglobalproduction4ggrtx7v",
            search_hub="prod-jobs-search-hub",
            token_url="https://www.ust.com/services/search",
        )
        assert cfg.organization_id == "ustglobalproduction4ggrtx7v"
        assert cfg.search_hub == "prod-jobs-search-hub"
        assert cfg.token_url == "https://www.ust.com/services/search"

    def test_frozen(self) -> None:
        cfg = CoveoConfig(
            organization_id="ustglobalproduction4ggrtx7v",
            search_hub="prod-jobs-search-hub",
            token_url="https://www.ust.com/services/search",
        )
        with pytest.raises(ValidationError):
            cfg.search_hub = "other"  # type: ignore[misc]

    def test_extra_field_forbidden(self) -> None:
        # The rest of the wire contract (``numberOfResults``, the
        # facet object, ``sortCriteria``, …) lives in the adapter's
        # body builder. Adding one here would split the contract
        # across two files — the PhenomConfig/TalentbrewConfig
        # discipline.
        with pytest.raises(ValidationError):
            CoveoConfig(  # type: ignore[call-arg]
                organization_id="ustglobalproduction4ggrtx7v",
                search_hub="prod-jobs-search-hub",
                token_url="https://www.ust.com/services/search",
                number_of_results=100,
            )


class TestCoveoStrategyPresenceValidator:
    """SYS-18: ``strategy="coveo"`` ⇔ ``coveo`` config, both directions.

    Presence is the *entire* schema-time gate. There are two reasons no
    host validator belongs here, and both are stronger than the
    Phenom/Talentbrew case: the search host is *derived* from
    ``organization_id`` rather than supplied, and the token-mint path
    lives on the tenant's own domain with documented per-tenant
    variance. An origin coupling between ``token_url`` and
    ``job_board_url`` would additionally false-reject a tenant minting
    from a sibling subdomain — pinned by
    ``test_token_url_need_not_share_job_board_origin``.
    """

    def _coveo_config(self) -> CoveoConfig:
        return CoveoConfig(
            organization_id="ustglobalproduction4ggrtx7v",
            search_hub="prod-jobs-search-hub",
            token_url="https://www.ust.com/services/search",
        )

    def _coveo_company(self, **overrides: object) -> Company:
        defaults: dict[str, object] = {
            "name": "UST",
            "job_board_url": "https://www.ust.com/en/jobsearch",
            "sample_job_url": "https://www.ust.com/jobs/80708213",
            "strategy": "coveo",
            "coveo": self._coveo_config(),
        }
        defaults.update(overrides)
        return Company(**defaults)  # type: ignore[arg-type]

    def test_coveo_strategy_with_config_is_accepted(self) -> None:
        c = self._coveo_company()
        assert c.strategy == "coveo"
        assert c.coveo is not None
        assert c.coveo.organization_id == "ustglobalproduction4ggrtx7v"

    def test_no_link_rule_path_prefix_is_required(self) -> None:
        # Unlike Phenom (which synthesizes URLs from the prefix) and
        # Talentbrew (which filters anchors through it), this adapter
        # emits ``clickUri`` verbatim from the payload. A default
        # ``LinkRule`` must therefore be accepted.
        c = self._coveo_company()
        assert c.link_rule.path_prefix is None

    def test_token_url_need_not_share_job_board_origin(self) -> None:
        # The no-origin-coupling pin. A tenant minting from a sibling
        # subdomain is legal; a validator tying ``token_url`` to
        # ``job_board_url``'s origin would break it.
        c = self._coveo_company(
            coveo=CoveoConfig(
                organization_id="ustglobalproduction4ggrtx7v",
                search_hub="prod-jobs-search-hub",
                token_url="https://search.ust.com/services/search",
            ),
        )
        assert c.coveo is not None
        assert c.coveo.token_url is not None
        assert "search.ust.com" in c.coveo.token_url

    def test_coveo_strategy_without_config_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="requires a coveo="):
            Company(
                name="UST No Config",
                job_board_url="https://www.ust.com/en/jobsearch",
                sample_job_url="https://www.ust.com/jobs/80708213",
                strategy="coveo",
            )

    def test_config_on_dom_strategy_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="strategy='dom'"):
            Company(
                name="Dom With Coveo Config",
                job_board_url="https://example.com/careers",
                sample_job_url="https://example.com/jobs/1",
                coveo=self._coveo_config(),
            )

    def test_config_on_greenhouse_strategy_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="strategy='greenhouse'"):
            Company(
                name="GH With Coveo Config",
                job_board_url="https://job-boards.greenhouse.io/example",
                sample_job_url="https://job-boards.greenhouse.io/example/jobs/1",
                strategy="greenhouse",
                coveo=self._coveo_config(),
            )

    def test_config_on_talentbrew_strategy_is_rejected(self) -> None:
        # Cross-adapter symmetry with the talentbrew suite's mirror.
        with pytest.raises(ValidationError, match="strategy='talentbrew'"):
            Company(
                name="Talentbrew With Coveo Config",
                job_board_url="https://jobs.citi.com/search-jobs",
                sample_job_url="https://jobs.citi.com/job/heredia/role/287/1",
                strategy="talentbrew",
                talentbrew=TalentbrewConfig(
                    facet_id="3624060", facet_display="Costa Rica"
                ),
                link_rule=LinkRule(path_prefix="/job"),
                coveo=self._coveo_config(),
            )

    def test_talentbrew_config_on_coveo_strategy_is_rejected(self) -> None:
        # The reverse direction of the test above, so neither adapter's
        # config can ride along on the other's strategy.
        with pytest.raises(ValidationError, match="strategy='coveo'"):
            Company(
                name="Coveo With Talentbrew Config",
                job_board_url="https://www.ust.com/en/jobsearch",
                sample_job_url="https://www.ust.com/jobs/80708213",
                strategy="coveo",
                coveo=self._coveo_config(),
                talentbrew=TalentbrewConfig(
                    facet_id="3624060", facet_display="Costa Rica"
                ),
            )

    def test_default_coveo_is_none(self) -> None:
        c = Company(
            name="Example",
            job_board_url="https://example.com/careers",
            sample_job_url="https://example.com/jobs/1",
        )
        assert c.coveo is None

    def test_coveo_strategy_corpus_entries_are_whitelisted(self) -> None:
        # SYS-18 landed with no corpus opt-in and this assertion pinned
        # the empty set; SYS-19 is the follow-up integration it
        # anticipated, so it tightens into the whitelist mirror every
        # other API strategy uses.
        #
        # UST is the first tenant. Its regression artifact is the
        # recorded payload at ``tests/fixtures/api/coveo/ust.json`` plus
        # the respx cases in ``tests/unit/test_coveo.py``, deliberately
        # not a DOM snapshot: the board renders zero job anchors at any
        # viewport, so there is nothing for a page fixture to freeze.
        #
        # It is also the only corpus entry whose token is *borrowed from
        # a browser* rather than minted over HTTP — the C19 closure. A
        # second tenant on this strategy should be checked for which
        # token source it needs; ungated tenants still use ``token_url``.
        expected_coveo = {"UST"}
        actual_coveo = {c.name for c in COMPANIES if c.strategy == "coveo"}
        assert actual_coveo == expected_coveo, (
            f"strategy='coveo' corpus set drift: "
            f"unexpected={actual_coveo - expected_coveo}, "
            f"missing={expected_coveo - actual_coveo}"
        )
