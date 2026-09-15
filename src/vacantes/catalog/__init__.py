"""The company catalog: the ``COMPANIES`` list plus handle resolution.

The catalog is the data layer's *identity* surface: the canonical list of
companies in the corpus, and the helpers that resolve a
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

from vacantes.catalog.companies import COMPANIES
from vacantes.domain.company import Company, slugify

# ``slugify`` is defined in ``domain.company`` and re-exported here.
# It moved there when the persistence layer landed: ``persistence``
# projects the catalog into a ``companies`` table keyed by slug, and the
# layering rule gives it a route to ``domain`` but none to ``catalog``.
# Prefer ``Company.slug`` when you hold an entity; this function is for
# the callers that hold only a name string (a report's ``company``
# field, a capture script's ``--expected`` target).
__all__ = ["COMPANIES", "find_company", "slugify"]


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
