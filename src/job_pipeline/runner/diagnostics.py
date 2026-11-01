from __future__ import annotations

import ipaddress
import logging
import re
import sys
from urllib.parse import urlsplit, urlunsplit

_URL = re.compile(r"https?:[^\s<>\"']*", re.IGNORECASE)
_CREDENTIAL = re.compile(
    r"\b(?:authorization|proxy-authorization)\b[^\r\n]*"
    r"|\b(?:api[_-]?key|password|secret|(?:access[_-]?|refresh[_-]?)?token)"
    r"\b(?:\\*[\"'])?\s*[=:][^\r\n]*"
    r"|\bsk-[\w-]+",
    re.IGNORECASE,
)


def redact_url(url: str) -> str:
    try:
        if any(char.isspace() or ord(char) < 32 for char in url) or "\\" in url:
            return "<unparseable-url>"
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
        if parts.scheme.lower() not in {"http", "https"} or not host:
            return "<unparseable-url>"
        if ":" in host:
            ipaddress.IPv6Address(host)
            host = f"[{host}]"
        elif not re.fullmatch(r"[\w.-]+", host, re.ASCII):
            return "<unparseable-url>"
        authority = host if port is None else f"{host}:{port}"
        return urlunsplit((parts.scheme.lower(), authority, parts.path, "", ""))
    except ValueError:
        return "<unparseable-url>"


def redact_message(text: str, limit: int = 200) -> str:
    first_line = text.splitlines()[0] if text.splitlines() else ""
    safe = _URL.sub(lambda match: redact_url(match.group()), first_line)
    safe = _CREDENTIAL.sub("<redacted>", safe)
    return " ".join(safe.split())[: max(0, limit)]


class _RuntimeStderrHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = redact_message(record.getMessage(), limit=4096)
        except Exception:
            message = "<unformattable diagnostic>"
        record.msg = message
        record.message = message
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        try:
            sys.stderr.write(self.format(record) + "\n")
            sys.stderr.flush()
        except Exception:
            pass


def configure_logging(verbosity: int) -> None:
    if verbosity <= 0:
        return
    logger = logging.getLogger("job_pipeline")
    for handler in tuple(logger.handlers):
        if handler.name == "job_pipeline.cli":
            logger.removeHandler(handler)
            handler.close()
    handler = _RuntimeStderrHandler()
    handler.name = "job_pipeline.cli"
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logger.handlers.insert(0, handler)
    logger.setLevel(logging.INFO if verbosity == 1 else logging.DEBUG)
    logger.propagate = True
