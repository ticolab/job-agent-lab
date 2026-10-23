"""Pipeline defaults, with one constant for every operational ceiling and threshold.

The model selects the inexpensive default. Concurrency constants cap HTTP,
browser, model, and per-host work independently. The job timeout bounds one
posting. The retry limit and one-day backoff bound repeated failures. The fetch
timeout bounds one HTTP request. Minimum content length rejects boilerplate,
and the fingerprint prefix bounds duplicate matching work.
"""

from datetime import timedelta

from job_discovery.settings import DATABASE_ENV_VAR, DATABASE_PATH

DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_HTTP_CONCURRENCY = 10
DEFAULT_BROWSER_CONCURRENCY = 2
DEFAULT_MODEL_CONCURRENCY = 4
DEFAULT_PER_HOST_CONCURRENCY = 2
DEFAULT_JOB_TIMEOUT = 180
RETRY_LIMIT = 3
RETRY_BACKOFF = timedelta(days=1)
FETCH_TIMEOUT = timedelta(seconds=30)
MIN_CONTENT_CHARS = 400
FINGERPRINT_CHARS = 2000

__all__ = [
    "DATABASE_ENV_VAR",
    "DATABASE_PATH",
    "DEFAULT_BROWSER_CONCURRENCY",
    "DEFAULT_HTTP_CONCURRENCY",
    "DEFAULT_JOB_TIMEOUT",
    "DEFAULT_MODEL",
    "DEFAULT_MODEL_CONCURRENCY",
    "DEFAULT_PER_HOST_CONCURRENCY",
    "FETCH_TIMEOUT",
    "FINGERPRINT_CHARS",
    "MIN_CONTENT_CHARS",
    "RETRY_BACKOFF",
    "RETRY_LIMIT",
]
