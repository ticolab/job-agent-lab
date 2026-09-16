"""Unit tests for ``vacantes.extraction.dom.rules.derive_path_prefix``.

The three examples in the function's own docstring are the "contract" the
runtime, the capture script, and the snapshot suite all rely on. This
file locks them in as executable assertions, plus adds trailing-slash
and root-path edge cases that surfaced during the catalog audit.
"""

from __future__ import annotations

import pytest

from vacantes.extraction.dom.rules import derive_path_prefix


class TestDocstringExamples:
    """The three examples in the derive_path_prefix docstring."""

    def test_jobs_id_suffix(self) -> None:
        assert derive_path_prefix("/jobs/7540236-senior-eng") == "/jobs"

    def test_apply_id_suffix(self) -> None:
        assert derive_path_prefix("/apply/17760976583210110395Pmz") == "/apply"

    def test_multi_segment_prefix(self) -> None:
        # Trailing slash on the sample URL must not leak into the prefix.
        assert (
            derive_path_prefix("/careers/requirements/271/") == "/careers/requirements"
        )


class TestEdgeCases:
    """Cases that fall outside the docstring but are exercised in practice."""

    def test_trailing_slash_is_stripped_before_dirname(self) -> None:
        # Trailing slash + no id suffix: the last segment is still the
        # "job id" and gets stripped.
        assert derive_path_prefix("/jobs/7540/") == "/jobs"

    def test_root_only_path_becomes_root(self) -> None:
        # posixpath.dirname("") is "" — the ``or "/"`` fallback keeps the
        # matcher's same-origin path-startsWith check working.
        assert derive_path_prefix("/") == "/"

    def test_single_segment_becomes_root(self) -> None:
        # A URL that is only "/id" (no parent directory) also collapses to
        # the origin root.
        assert derive_path_prefix("/7540236") == "/"

    def test_full_url_is_accepted(self) -> None:
        # urlparse strips the scheme + netloc, so a fully-qualified URL
        # produces the same prefix as its path.
        assert (
            derive_path_prefix("https://boards.greenhouse.io/company/jobs/12345")
            == "/company/jobs"
        )


@pytest.mark.parametrize(
    ("sample", "expected"),
    [
        ("/jobs/7540-eng", "/jobs"),
        ("/apply/abc", "/apply"),
        ("/careers/requirements/271/", "/careers/requirements"),
        ("/en/sites/CX_1/job/3387", "/en/sites/CX_1/job"),
    ],
)
def test_parametrised_matrix(sample: str, expected: str) -> None:
    """Small matrix of real-world sample URLs from the current catalog."""
    assert derive_path_prefix(sample) == expected
