"""Pipeline defaults, with one constant for every operational ceiling and threshold.

The model selects the inexpensive default. Concurrency constants cap HTTP,
browser, model, and per-host work independently. The job timeout bounds each
network or model step of one posting once that step holds its slot; time spent
waiting for a slot is never charged against it, so a corpus-sized run cannot
time out jobs that are merely queued. The stale-attempt multiplier turns that
per-step bound into the ceiling shared by both stages. Eligibility holds at
most three timed steps and extraction one, so four timeouts safely bound each.
The retry limit and one-day backoff bound repeated failures. The fetch timeout
bounds one HTTP request. Minimum content length rejects boilerplate. Frame
capture bounds top-level candidates, visible dimensions, and each document read.
"""

from datetime import timedelta

from job_discovery.settings import DATABASE_ENV_VAR, DATABASE_PATH

DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_HTTP_CONCURRENCY = 10
DEFAULT_BROWSER_CONCURRENCY = 2
DEFAULT_MODEL_CONCURRENCY = 4
DEFAULT_PER_HOST_CONCURRENCY = 2
DEFAULT_JOB_TIMEOUT = 180
STALE_ATTEMPT_PHASES = 4
RETRY_LIMIT = 3
RETRY_BACKOFF = timedelta(days=1)
FETCH_TIMEOUT = timedelta(seconds=30)
MIN_CONTENT_CHARS = 400
MAX_CAPTURED_FRAMES = 10
MIN_FRAME_WIDTH_PX = 100
MIN_FRAME_HEIGHT_PX = 100
FRAME_READ_TIMEOUT = timedelta(seconds=5)

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
    "MAX_CAPTURED_FRAMES",
    "MIN_FRAME_WIDTH_PX",
    "MIN_FRAME_HEIGHT_PX",
    "FRAME_READ_TIMEOUT",
    "RETRY_BACKOFF",
    "RETRY_LIMIT",
    "STALE_ATTEMPT_PHASES",
]
