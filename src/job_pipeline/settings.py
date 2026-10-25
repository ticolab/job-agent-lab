"""Pipeline defaults, with one constant for every operational ceiling and threshold.

The model selects the inexpensive default. Concurrency constants cap HTTP,
browser, model, and per-host work independently. The job timeout bounds each
network or model step of one posting once that step holds its slot; time spent
waiting for a slot is never charged against it, so a corpus-sized run cannot
time out jobs that are merely queued. The stale-attempt multiplier turns that
per-step bound into the ceiling on one whole attempt, which is what the reaper
measures abandoned attempts against. The retry limit and one-day backoff bound
repeated failures. The fetch timeout bounds one HTTP request. Minimum content
length rejects boilerplate.
"""

from datetime import timedelta

from job_discovery.settings import DATABASE_ENV_VAR, DATABASE_PATH

DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_HTTP_CONCURRENCY = 10
DEFAULT_BROWSER_CONCURRENCY = 2
DEFAULT_MODEL_CONCURRENCY = 4
DEFAULT_PER_HOST_CONCURRENCY = 2
DEFAULT_JOB_TIMEOUT = 180
# One attempt holds at most four timed steps: plain fetch, rendered fetch,
# eligibility call, extraction call. An unfinished attempt older than four
# timeouts cannot be live and is reaped as interrupted.
STALE_ATTEMPT_PHASES = 4
RETRY_LIMIT = 3
RETRY_BACKOFF = timedelta(days=1)
FETCH_TIMEOUT = timedelta(seconds=30)
MIN_CONTENT_CHARS = 400

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
    "MIN_CONTENT_CHARS",
    "RETRY_BACKOFF",
    "RETRY_LIMIT",
    "STALE_ATTEMPT_PHASES",
]
