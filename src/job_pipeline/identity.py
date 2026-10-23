"""Stable URL and content identities for discovered postings."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from job_pipeline.settings import FINGERPRINT_CHARS

NON_IDENTIFYING_PARAMS = frozenset(
    {
        "title",  # Accenture
        "name",  # Techwarely
        "jobName",  # Team Talent
        "lastSelectedFacet",  # Akamai, Emerson, Fortinet
        "selectedLocationsFacet",  # Akamai, Emerson, Fortinet
        "in_iframe",  # Auxis, Avionyx iCIMS wrappers
        "locationCountry",  # Babel, Cloudera, LSEG, Medtronic
        "locationId",  # Kraken, Luxor, SimSpace
        "source",  # GoLabs Tech
        "new",  # GoLabs Tech
        "trid",  # Publicis Sapient
        "locations",  # Amex GBT, APM Terminals, Intel, World Kinect
        "mode",  # Akamai
        "l",  # SentinelOne
        "lang",  # BAC
        "c",  # BAC, Oliver Healthcare Packaging
        "export-pdf",  # Excel Nearshore
    }
)

_WHITESPACE = re.compile(r"\s+")


def _normalized_identity_text(value: str) -> str:
    return _WHITESPACE.sub(" ", value.casefold()).strip()


def url_key(url: str) -> str:
    """Return a canonical key while preserving identifying URL components."""
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    host = parsed.hostname
    if scheme not in {"http", "https"} or host is None:
        raise ValueError("URL must be an absolute HTTP(S) URL")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("URL has an invalid port") from exc

    netloc = host.lower()
    if ":" in netloc and not netloc.startswith("["):
        netloc = f"[{netloc}]"
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        netloc = f"{netloc}:{port}"

    path = parsed.path
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    query = sorted(
        (name, value)
        for name, value in parse_qsl(parsed.query, keep_blank_values=True)
        if name not in NON_IDENTIFYING_PARAMS and not name.startswith("utm_")
    )
    return urlunsplit((scheme, netloc, path, urlencode(query, doseq=True), ""))


def fingerprint(title: str, text: str) -> str:
    """Hash normalized title and the bounded normalized posting prefix."""
    normalized = (
        f"{_normalized_identity_text(title)}\n"
        f"{_normalized_identity_text(text)[:FINGERPRINT_CHARS]}"
    )
    return hashlib.sha256(normalized.encode()).hexdigest()
