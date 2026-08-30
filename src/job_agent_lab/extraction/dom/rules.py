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
