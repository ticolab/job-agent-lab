"""The company catalog: the ``COMPANIES`` list plus handle resolution.

The catalog is the data layer's *identity* surface: the canonical list of
companies the lab tests against, and the helpers that resolve a
user-supplied handle (``-c gap``) to a specific entry. Handle resolution
lives here — not in ``cli`` — because ``scripts/capture_snapshot.py`` also
needs it.

Handle-matching precedence, preserved from the pre-SYS-1 CLI:

1. exact match against any ``aliases`` entry (case-insensitive),
2. exact match against the company's auto-derived acronym,
3. substring match against the company's ``name``.

The first entry in ``COMPANIES`` matching the earliest rule wins; misses
return ``None``.
"""

from __future__ import annotations

import re

from job_agent_lab.catalog.companies import COMPANIES
from job_agent_lab.domain.company import Company

__all__ = ["COMPANIES", "find_company", "slugify"]


def slugify(name: str) -> str:
    """Convert a company name to a filesystem-safe slug.

    Used for output filenames (``output/<slug>_<timestamp>.json``) and for
    snapshot directories (``tests/fixtures/snapshots/<slug>/``). The rule
    is deliberately simple — lowercase, non-alphanumerics collapsed to a
    single underscore, no leading/trailing underscore — so slugs are
    stable across renames of unrelated fields.
    """
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _acronym(name: str) -> str:
    """Build a lowercase acronym from a company name (e.g. "GAP")."""
    return "".join(word[0] for word in name.split() if word).lower()


def find_company(query: str) -> Company | None:
    """Find a company by a flexible, case-insensitive handle.

    Matches, in order: an exact alias, the acronym of the name, then a
    substring of the full name. So ``"gap"``, ``"Growth"``, and
    ``"growth acceleration"`` all resolve to
    ``"Growth Acceleration Partners"``.
    """
    q = query.strip().lower()
    for company in COMPANIES:
        if q in [a.lower() for a in company.aliases]:
            return company
        if q == _acronym(company.name):
            return company
        if q in company.name.lower():
            return company
    return None
