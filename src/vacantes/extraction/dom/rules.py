"""Link-shape rules for the DOM matcher.

``derive_path_prefix`` is the default heuristic used when a ``Company``
entry does not set an explicit ``LinkRule.path_prefix`` override: it
takes the parent directory of the sample job URL's path and uses that
as the same-origin prefix that valid job links must start with.

Kept public (no leading underscore) because the snapshot capture script
and the ``integrate-company`` skill's ground-truth helper both call it
directly to preview the prefix a new company entry will resolve to.
"""

from __future__ import annotations

import posixpath
from urllib.parse import urlparse


def derive_path_prefix(sample_job_url: str) -> str:
    """Derive the job-link path prefix from a sample job URL.

    Takes the parent directory of the sample URL's path. For example:
    - "/jobs/7540236-senior-eng" -> "/jobs"
    - "/apply/17760976583210110395Pmz" -> "/apply"
    - "/careers/requirements/271/" -> "/careers/requirements"
    """
    path = urlparse(sample_job_url).path.rstrip("/")
    return posixpath.dirname(path) or "/"


def is_posting_url(
    current_url: str,
    *,
    origin: str,
    base_path: str,
    min_depth: int = 1,
    listing_url: str,
) -> bool:
    """Return ``True`` iff *current_url* is itself a job posting, not a listing.

    The guard behind the agent's ``extract_job_links`` tool. The matcher
    reports whatever anchors are on the page it is handed and has no
    notion of *which* page that is; when the agent wanders into a
    posting's detail page and calls the tool there, the matcher
    truthfully finds the one job link on it — the page's own — and
    returns a count of 1 that nothing downstream can distinguish from a
    small board. This predicate lets the tool refuse that call with an
    error the agent can act on, instead of returning a confident 1.

    It answers one question: *would the matcher's own id-in-path rule
    collect this page's URL as a posting?* That rule is mirrored here
    exactly — same-origin, a single trailing slash stripped (slice form,
    matching JS ``.replace(/\\/$/, '')`` rather than ``rstrip``, which
    would diverge on ``//``), ``startswith(base_path + "/")``, and a
    remainder of at least ``min_depth`` segments — so the guard and the
    matcher cannot disagree about what a posting looks like.

    Two shapes are deliberately **not** treated as postings:

    - The **id-in-query** shape (path equals ``base_path`` with a query).
      Twenty-eight corpus *listing* URLs have that form — Ashby's
      ``?locationId=``, Lever's ``?location=``, every ``?country=`` board
      — because a filtered listing root looks exactly like a
      query-keyed posting. The matcher resolves the same ambiguity by
      preferring the path bucket; the guard resolves it by ignoring the
      query shape altogether.
    - The board's **own listing path** (``listing_url``), compared by path
      so an agent-applied filter query does not defeat the exemption.
      Team Talent's listing ``/teamsites/our-openings`` sits under its
      own prefix ``/teamsites`` at posting depth; without this exemption
      the guard would refuse to extract on a working board.

    Both exclusions are pinned corpus-wide by
    ``tests/unit/test_posting_guard.py`` against every catalog listing
    URL and every recorded pagination state, so a future entry whose
    listing trips the guard fails at test time rather than live.

    Args:
        current_url: The URL of the page the tool is being invoked on.
        origin: ``scheme://host`` of the board; a different origin is
            never a posting of this board.
        base_path: The effective job-link prefix (explicit override or
            :func:`derive_path_prefix`).
        min_depth: ``LinkRule.min_depth``; the id-in-path depth floor.
        listing_url: ``Company.job_board_url``; its path is exempt.
    """
    try:
        cur = urlparse(current_url)
        lst = urlparse(listing_url)
    except ValueError:
        return False
    if not cur.scheme or not cur.netloc:
        return False
    if f"{cur.scheme}://{cur.netloc}" != origin:
        return False

    def _strip_one(path: str) -> str:
        return path[:-1] if path.endswith("/") else path

    path = _strip_one(cur.path)
    if path == _strip_one(lst.path):
        return False
    if not path.startswith(base_path + "/"):
        return False
    remainder = path[len(base_path) + 1 :]
    return len(remainder.split("/")) >= min_depth
