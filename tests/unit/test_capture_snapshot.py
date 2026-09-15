"""Unit tests for the ``scripts/capture_snapshot.py`` capture driver.

The capture script is not a package member — it lives under ``scripts/``
and is invoked directly by the ``uv run python scripts/capture_snapshot.py``
integration workflow. To exercise its pure helpers from pytest we load
it as a sibling file via :mod:`importlib.util`, following the precedent
set by ``scripts/verify_expand_selector.py`` and ``scripts/verify_capture_v2.py``.

Coverage today is the SYS-12 ``resolve_expand_selector`` precedence
rule: the ``--expand-selector`` CLI flag overrides
``Company.hooks.expand_selector`` when both are present; the catalog
value is used when only the catalog is set; ``None`` when neither is.
Four cases enumerate the truth table so a future refactor cannot
silently invert the precedence — the flag-beats-catalog asymmetry is a
load-bearing part of the integration workflow (iterate on the selector
with the flag, then move the settled value into the catalog).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from job_agent_lab.domain.company import RuntimeHooks

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
_CAPTURE_PATH = _SCRIPTS_DIR / "capture_snapshot.py"
_spec = importlib.util.spec_from_file_location("capture_snapshot", _CAPTURE_PATH)
assert _spec is not None and _spec.loader is not None
_capture_module = importlib.util.module_from_spec(_spec)
# Register in ``sys.modules`` before executing so any internal
# ``from capture_snapshot import ...`` (there are none today, but the
# pattern survives a future refactor) resolves correctly.
sys.modules["capture_snapshot"] = _capture_module
_spec.loader.exec_module(_capture_module)
resolve_expand_selector = _capture_module.resolve_expand_selector


class TestResolveExpandSelectorPrecedence:
    """Enumerate the four (hook × flag) input cases for the resolver.

    The resolver is a two-line pure function; these tests exist to pin
    the *precedence* (flag > catalog) rather than the mechanics. If the
    body is ever rewritten (e.g. to accept a third source) the
    truth-table shape must stay stable — the integration workflow's
    "iterate with the flag, commit with the catalog" contract depends
    on it.
    """

    def test_neither_hook_nor_flag_returns_none(self) -> None:
        """Inert hook + no flag → no expansion runs during capture.

        This is the pre-SYS-6 baseline: every company whose entry does
        not opt into expansion, invoked without the flag, produces a
        byte-stable v2 fixture with no ``expand_selector`` metadata key
        emitted (guaranteed downstream in ``_write_snapshot``'s
        conditional-key block).
        """
        hooks = RuntimeHooks()
        assert resolve_expand_selector(hooks, None) is None

    def test_flag_only_returns_flag(self) -> None:
        """Inert hook + flag set → flag drives the capture.

        The pre-SYS-12 ``--expand-selector`` use case: an integrator
        discovers the selector via probe/GT iteration and re-runs the
        capture with the flag before touching the catalog. The flag's
        value is what mounts the anchors.
        """
        hooks = RuntimeHooks()
        assert resolve_expand_selector(hooks, "button.accordion") == "button.accordion"

    def test_catalog_only_returns_catalog(self) -> None:
        """Non-inert hook + no flag → catalog value drives the capture.

        The steady-state SYS-12 case: the selector has been settled and
        moved into ``Company.hooks.expand_selector``; the integrator
        re-captures without the flag and the same effective selector
        still runs. This is how a hook-configured board's fixture is
        maintained across matcher upgrades without CLI-flag ceremony.
        """
        hooks = RuntimeHooks(expand_selector=".dept-toggle[aria-expanded='false']")
        assert (
            resolve_expand_selector(hooks, None)
            == ".dept-toggle[aria-expanded='false']"
        )

    def test_flag_overrides_catalog(self) -> None:
        """Non-inert hook + flag set → flag wins over catalog.

        The iteration case: the catalog holds an outdated selector but
        the integrator is trying a new one via the flag. The flag must
        win so the capture reflects the new attempt without a two-step
        edit-catalog-then-re-run loop. The equality assertion also
        pins that the resolver returns the flag verbatim rather than
        merging or concatenating the two values.
        """
        hooks = RuntimeHooks(expand_selector="button.old-selector")
        result = resolve_expand_selector(hooks, "button.new-selector")
        assert result == "button.new-selector"
        # Belt-and-braces: the catalog value must not leak through in
        # any form (substring, prefix, tuple, etc.). The resolver is a
        # pure override, not a combinator.
        assert "old-selector" not in result


class TestResolveExpandSelectorEmptyString:
    """The empty string is a valid CSS selector for "match nothing".

    ``RuntimeHooks.expand_selector`` is typed ``str | None``; pydantic's
    default schema does not reject the empty string. The resolver must
    treat ``""`` as a distinct value from ``None`` — otherwise a
    catalog entry explicitly opting out with ``expand_selector=""``
    would silently fall back to the flag, which is not the documented
    contract. Guard this behaviour so a future "coerce empty to None"
    optimization doesn't silently regress it.
    """

    def test_empty_string_flag_wins_over_hook(self) -> None:
        """Flag=`""` is truthy-as-present under the ``is not None`` rule.

        The resolver's precedence check is ``flag is not None``, not
        ``flag``; an empty-string flag counts as "set" and wins over
        the catalog. This edge case is a knob for testing the
        no-expansion path without touching the catalog — pass
        ``--expand-selector ''`` to force zero clicks even when the
        catalog defines a selector.
        """
        hooks = RuntimeHooks(expand_selector="button.something")
        assert resolve_expand_selector(hooks, "") == ""

    def test_empty_string_hook_returned_when_no_flag(self) -> None:
        """Hook=`""` with no flag returns the empty string.

        Symmetric with the flag-empty case: the resolver returns
        whatever the source contains, without coercing empty to None.
        The downstream ``expand_all`` call handles empty selectors
        harmlessly (zero visible matches → immediate loop termination
        → zero clicks), so the empty-string path is safe end-to-end.
        """
        hooks = RuntimeHooks(expand_selector="")
        assert resolve_expand_selector(hooks, None) == ""


class TestPreFilterUrlsMetadata:
    """Pin the SYS-13 fixture-shape contract in ``_write_snapshot``.

    The runtime code path for a declaring ``Company`` (non-empty
    ``pre_filter_urls``) is agent-less: :func:`DomStrategy._extract_prefiltered`
    unions per-URL matcher runs and reports ``states_visited``. The
    capture-side counterpart must freeze the corresponding fixture layout
    so ``tests/snapshots/test_extractor_snapshots.py`` can replay each
    state under the correct base href and union the matcher outputs the
    same way. Three additive-optional metadata keys carry that contract:

    - ``pre_filter_urls`` — verbatim from ``Company.pre_filter_urls``
      so the runtime code path is auditable from the fixture alone
    - ``top_url`` — the URL state 1 was rendered from (which is
      ``pre_filter_urls[0]``, not ``job_board_url`` — the runtime
      never visits ``job_board_url`` on this path, and the harness
      must replay ``page.html`` under the same base href)
    - ``states`` — the list of ``{file, url}`` entries for states 2..N,
      mirroring the ``frames`` / ``pages`` convention

    Non-declaring captures skip all three so every existing SYS-2/5/6/12
    fixture stays byte-identical under SYS-13's schema. The tests below
    exercise both branches plus the ``states/`` stale-file hygiene that
    parallels the ``pages/`` sweep — a fixture converted from declaring
    back to single-shot must not carry orphan ``state-N.html`` files.
    """

    def _stub_company(self, pre_filter_urls: tuple[str, ...] = ()) -> Company:  # type: ignore[name-defined]  # noqa: F821
        """Build a minimal ``Company`` with the given prefilter URLs.

        Kept as a helper so the three tests all share the same field
        set and the SYS-13-specific field is the only variable across
        cases — anything else changing would confound the assertions.
        """
        from job_agent_lab.domain.company import Company

        return Company(
            name="Example Corp",
            job_board_url="https://example.com/careers",
            sample_job_url="https://example.com/jobs/1",
            pre_filter_urls=pre_filter_urls,
        )

    def test_non_declaring_capture_omits_sys13_keys(self, tmp_path: Path) -> None:
        """Empty ``pre_filter_urls`` + empty ``states`` → no SYS-13 keys.

        This is the byte-stability invariant: every pre-SYS-13 fixture
        (SYS-2 single-shot, SYS-5 paginated, SYS-6 expand-on-capture,
        SYS-12 hook-configured) must continue to produce metadata with
        the exact same key set after the SYS-13 changes land. If any of
        ``pre_filter_urls`` / ``top_url`` / ``states`` appears here the
        additive-optional contract is broken and every existing fixture
        would need to be recaptured to stay in sync.
        """
        import json

        write_snapshot = _capture_module._write_snapshot
        company = self._stub_company()
        write_snapshot(
            tmp_path,
            company,
            "<html><body>state 1</body></html>",
            [],  # frames
            [],  # pages
            [],  # states
            0,  # expected
            "unit test",
        )
        metadata = json.loads((tmp_path / "metadata.json").read_text())
        assert "pre_filter_urls" not in metadata
        assert "top_url" not in metadata
        assert "states" not in metadata
        # And the ``states/`` directory must not be materialised on a
        # non-declaring capture — the hygiene sweep runs but no files
        # are written, so the directory should stay absent.
        assert not (tmp_path / "states").exists()

    def test_declaring_capture_emits_sys13_keys_and_state_files(
        self, tmp_path: Path
    ) -> None:
        """Declaring capture → all three keys + ``states/state-N.html`` on disk.

        Pins the full contract in one shot: metadata carries the tuple
        verbatim (as a JSON list — pydantic tuples serialize as lists),
        ``top_url`` matches ``pre_filter_urls[0]`` exactly, ``states``
        enumerates each state-N file with its rendered URL, and each
        referenced HTML file actually exists on disk with the expected
        body. The referenced-file existence check catches a whole class
        of "metadata claims a file the harness will fail to load" bugs.
        """
        import json

        write_snapshot = _capture_module._write_snapshot
        pre_filter_urls = (
            "https://example.com/careers?location=cr",
            "https://example.com/careers?location=latam",
        )
        company = self._stub_company(pre_filter_urls=pre_filter_urls)
        states: list[
            tuple[int, str, str, list[tuple[str, str, str]], list[tuple[int, str, str]]]
        ] = [
            (2, pre_filter_urls[1], "<html><body>state 2</body></html>", [], []),
        ]
        write_snapshot(
            tmp_path,
            company,
            "<html><body>state 1</body></html>",
            [],  # frames
            [],  # pages
            states,
            5,  # expected (arbitrary — union count, unused by this test)
            "unit test",
        )
        metadata = json.loads((tmp_path / "metadata.json").read_text())
        # Tuple → list on the JSON boundary; equality still holds
        # elementwise. The verbatim recording is the auditability contract.
        assert metadata["pre_filter_urls"] == list(pre_filter_urls)
        # ``top_url`` is the runtime's state-1 URL, which must equal
        # ``pre_filter_urls[0]`` — the harness relies on this equality to
        # replay ``page.html`` under the correct base href.
        assert metadata["top_url"] == pre_filter_urls[0]
        # ``states`` mirrors ``frames`` / ``pages`` shape: list of
        # ``{file, url}`` dicts. One entry per state-N ≥ 2.
        # A state with no same-origin frames emits no ``frames`` key, so
        # the entry shape is byte-identical to its pre-per-state-frames
        # form. Every multi-state fixture whose anchors live in the top
        # document stays comparable.
        assert metadata["states"] == [
            {"file": "states/state-2.html", "url": pre_filter_urls[1]}
        ]
        # And the referenced file must actually be on disk with the
        # right body. The harness reads the file straight from the path
        # in ``metadata.states[*].file``.
        state_2_path = tmp_path / "states" / "state-2.html"
        assert state_2_path.exists()
        assert state_2_path.read_text() == "<html><body>state 2</body></html>"

    def test_stale_state_files_swept_on_non_declaring_recapture(
        self, tmp_path: Path
    ) -> None:
        """A prior declaring capture's ``states/`` is emptied on re-capture.

        Mirrors the ``pages/`` hygiene contract: if an integrator flips
        a company from declaring back to non-declaring (or drops one URL
        from the tuple) and re-captures, the fixture must not carry
        orphan ``state-N.html`` files. The harness would happily union
        them back in, silently over-counting. The sweep runs
        unconditionally in ``_write_snapshot``, so a re-capture with
        empty ``states`` clears the directory to empty even though no
        new state files are written.
        """
        write_snapshot = _capture_module._write_snapshot
        # Seed a stale ``states/`` directory as if from a prior capture.
        stale_dir = tmp_path / "states"
        stale_dir.mkdir()
        stale_file = stale_dir / "state-2.html"
        stale_file.write_text("<html><body>stale</body></html>")
        assert stale_file.exists()
        # Re-capture with no ``pre_filter_urls`` and no ``states``. The
        # sweep block must run and delete the stale file.
        company = self._stub_company()
        write_snapshot(
            tmp_path,
            company,
            "<html><body>fresh state 1</body></html>",
            [],
            [],
            [],
            0,
            "unit test",
        )
        assert not stale_file.exists()
        # The directory itself may remain (the sweep only unlinks files)
        # — the harness enumerates via ``metadata.states``, so an empty
        # directory is functionally invisible. This assertion pins the
        # file-only-sweep semantics rather than any directory removal.
        assert stale_dir.exists()


class TestSuppressAncestorSelectorMetadata:
    """Pin the SYS-14 additive-optional metadata key in ``_write_snapshot``.

    A fixture captured from a board with container suppression active
    (Ulteig / C16) has the suppressed section *absent from its recorded
    count* — the capture-side matcher run excluded it. The harness must
    therefore replay that fixture under the same selector, which it can
    only do if the selector is recorded. Without the key, replay would
    count the featured section the capture excluded and every run would
    fail by exactly the section's size.

    The converse is the byte-stability invariant: a board with no
    suppression must emit no key, so every pre-SYS-14 fixture keeps its
    exact metadata key set.
    """

    def _stub_company(self, suppress: str | None = None) -> Company:  # type: ignore[name-defined]  # noqa: F821
        from job_agent_lab.domain.company import Company, LinkRule

        return Company(
            name="Example Corp",
            job_board_url="https://example.com/careers",
            sample_job_url="https://example.com/jobs/1",
            link_rule=LinkRule(suppress_ancestor_selector=suppress),
        )

    def test_default_link_rule_omits_the_key(self, tmp_path: Path) -> None:
        import json

        _capture_module._write_snapshot(
            tmp_path,
            self._stub_company(),
            "<html><body>state 1</body></html>",
            [],
            [],
            [],
            0,
            "unit test",
        )
        metadata = json.loads((tmp_path / "metadata.json").read_text())
        assert "suppress_ancestor_selector" not in metadata

    def test_configured_selector_is_recorded_verbatim(self, tmp_path: Path) -> None:
        import json

        selector = '[data-automation="featured-opportunities"]'
        _capture_module._write_snapshot(
            tmp_path,
            self._stub_company(selector),
            "<html><body>state 1</body></html>",
            [],
            [],
            [],
            0,
            "unit test",
        )
        metadata = json.loads((tmp_path / "metadata.json").read_text())
        # Verbatim — the harness passes it straight to the matcher, so
        # any normalisation here would silently change replay semantics.
        assert metadata["suppress_ancestor_selector"] == selector


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestPerStateFrames:
    """Pin per-state frame freezing in ``_write_snapshot``.

    Through SYS-13 the capture script froze same-origin frames for state
    1 only, and the docstring called frames on states >= 2 a documented
    non-goal. That held as long as no corpus board combined the two axes:
    every frame-bearing fixture was single-state, and every multi-state
    fixture kept its anchors in the top document.

    Auxis breaks the assumption. It is an iCIMS portal whose entire
    listing renders inside ``#icims_content_iframe``, paged by a ``?pr=N``
    URL cursor — so it needs ``pre_filter_urls`` for the pages *and* frame
    descent for the anchors. With state-1-only frames its state 2 froze as
    an anchorless shell and the fixture replayed 10 of 11 links while the
    live runtime returned all 11: a capture-side gap only, since the
    matcher asset already descends same-origin frames in-page at every
    state.

    The fix keys off the same additive-optional convention as every other
    schema key: a state entry gains a nested ``frames`` list only when
    that state actually carries anchor-bearing frames, so multi-state
    fixtures without frames keep byte-identical metadata (pinned by
    ``TestPreFilterUrlsMetadata``).
    """

    def _stub_company(self, pre_filter_urls: tuple[str, ...] = ()) -> Company:  # type: ignore[name-defined]  # noqa: F821
        from job_agent_lab.domain.company import Company

        return Company(
            name="Example Corp",
            job_board_url="https://example.com/careers",
            sample_job_url="https://example.com/jobs/1",
            pre_filter_urls=pre_filter_urls,
        )

    def test_state_frames_are_written_and_recorded(self, tmp_path: Path) -> None:
        """A state carrying frames emits a nested ``frames`` list + files.

        The path shape (``states/state-N-frames/<name>.html``) matters as
        much as the metadata: the harness reads each frame straight from
        ``metadata.states[*].frames[*].file``, so a mismatch between the
        recorded path and the written file is exactly the "metadata claims
        a file the harness cannot load" bug the SYS-13 tests guard against
        one level up.
        """
        import json

        write_snapshot = _capture_module._write_snapshot
        pre_filter_urls = (
            "https://example.com/careers?pr=0",
            "https://example.com/careers?pr=1",
        )
        company = self._stub_company(pre_filter_urls=pre_filter_urls)
        states: list[
            tuple[int, str, str, list[tuple[str, str, str]], list[tuple[int, str, str]]]
        ] = [
            (
                2,
                pre_filter_urls[1],
                "<html><body>state 2 shell</body></html>",
                [
                    (
                        "6-board_iframe",
                        "https://example.com/careers?pr=1&in_iframe=1",
                        "<html><body><a href='/jobs/2'>Job</a></body></html>",
                    )
                ],
                [],  # pages (no walker within this state)
            ),
        ]
        write_snapshot(
            tmp_path,
            company,
            "<html><body>state 1</body></html>",
            [],  # frames (state 1)
            [],  # pages
            states,
            2,
            "unit test",
        )
        metadata = json.loads((tmp_path / "metadata.json").read_text())
        assert metadata["states"] == [
            {
                "file": "states/state-2.html",
                "url": pre_filter_urls[1],
                "frames": [
                    {
                        "file": "states/state-2-frames/6-board_iframe.html",
                        "url": "https://example.com/careers?pr=1&in_iframe=1",
                    }
                ],
            }
        ]
        frame_path = tmp_path / "states" / "state-2-frames" / "6-board_iframe.html"
        assert frame_path.exists()
        assert "href='/jobs/2'" in frame_path.read_text()

    def test_stale_state_frames_swept_on_recapture(self, tmp_path: Path) -> None:
        """Orphan per-state frame files are deleted on re-capture.

        Same silent-over-count hazard the ``states/`` and ``pages/``
        sweeps exist for, one directory level deeper: a board that drops
        a state (or whose state stops carrying an iframe) would otherwise
        leave a frame file behind that the harness keeps unioning in.
        """
        write_snapshot = _capture_module._write_snapshot
        stale_dir = tmp_path / "states" / "state-2-frames"
        stale_dir.mkdir(parents=True)
        stale_file = stale_dir / "6-board_iframe.html"
        stale_file.write_text("<html><body><a href='/jobs/99'>Stale</a></body></html>")
        write_snapshot(
            tmp_path,
            self._stub_company(),
            "<html><body>fresh state 1</body></html>",
            [],
            [],
            [],
            0,
            "unit test",
        )
        assert not stale_file.exists()


class TestPerStatePages:
    """Pin per-state page freezing in ``_write_snapshot`` (declaring × paginate).

    Accenture is the first board to need both axes at once: one
    ``pre_filter_urls`` state whose listing is paged behind a ``Next``
    button that never changes the URL, so the pages cannot be
    decomposed into further pre-filter URLs. The runtime already
    composes the two (``_extract_prefiltered`` forwards
    ``Company.paginate`` into every per-state ``collect_job_links``);
    this pins the capture-side counterpart: a state entry gains a
    nested ``pages`` list only when the walker collected states >= 2
    within it, written under ``states/state-N-pages/page-M.html`` — the
    sibling-directory convention ``state-N-frames/`` established — so
    every fixture without the combination keeps byte-identical
    metadata.
    """

    def _stub_company(self, pre_filter_urls: tuple[str, ...] = ()) -> Company:  # type: ignore[name-defined]  # noqa: F821
        from job_agent_lab.domain.company import Company

        return Company(
            name="Example Corp",
            job_board_url="https://example.com/careers",
            sample_job_url="https://example.com/jobs/1",
            pre_filter_urls=pre_filter_urls,
        )

    def test_state_pages_are_written_and_recorded(self, tmp_path: Path) -> None:
        """A state carrying walker pages emits a nested ``pages`` list + files.

        Path shape matters as much as the metadata: the harness reads
        each page straight from ``metadata.states[*].pages[*].file``.
        """
        import json

        write_snapshot = _capture_module._write_snapshot
        pre_filter_urls = (
            "https://example.com/careers?region=cr",
            "https://example.com/careers?region=latam",
        )
        company = self._stub_company(pre_filter_urls=pre_filter_urls)
        states: list[
            tuple[int, str, str, list[tuple[str, str, str]], list[tuple[int, str, str]]]
        ] = [
            (
                2,
                pre_filter_urls[1],
                "<html><body>state 2 page 1</body></html>",
                [],
                [
                    (
                        2,
                        pre_filter_urls[1],
                        "<html><body>state 2 page 2</body></html>",
                    ),
                    (
                        3,
                        pre_filter_urls[1],
                        "<html><body>state 2 page 3</body></html>",
                    ),
                ],
            ),
        ]
        write_snapshot(
            tmp_path,
            company,
            "<html><body>state 1</body></html>",
            [],  # frames (state 1)
            [],  # pages (state 1)
            states,
            0,
            "unit test",
        )
        metadata = json.loads((tmp_path / "metadata.json").read_text())
        assert metadata["states"] == [
            {
                "file": "states/state-2.html",
                "url": pre_filter_urls[1],
                "pages": [
                    {
                        "file": "states/state-2-pages/page-2.html",
                        "url": pre_filter_urls[1],
                    },
                    {
                        "file": "states/state-2-pages/page-3.html",
                        "url": pre_filter_urls[1],
                    },
                ],
            }
        ]
        for page_idx in (2, 3):
            page_path = tmp_path / "states" / "state-2-pages" / f"page-{page_idx}.html"
            assert page_path.exists()
            assert f"state 2 page {page_idx}" in page_path.read_text()

    def test_stale_state_pages_swept_on_recapture(self, tmp_path: Path) -> None:
        """Orphan per-state page files are deleted on re-capture.

        Same silent-over-count hazard the ``states/``, ``pages/``, and
        ``state-N-frames/`` sweeps exist for: a state whose walker now
        finds fewer pages must not leave a stale ``page-M.html`` behind
        for the harness to union back in.
        """
        write_snapshot = _capture_module._write_snapshot
        stale_dir = tmp_path / "states" / "state-2-pages"
        stale_dir.mkdir(parents=True)
        stale_file = stale_dir / "page-7.html"
        stale_file.write_text("<html><body><a href='/jobs/99'>Stale</a></body></html>")
        write_snapshot(
            tmp_path,
            self._stub_company(),
            "<html><body>fresh state 1</body></html>",
            [],
            [],
            [],
            0,
            "unit test",
        )
        assert not stale_file.exists()
