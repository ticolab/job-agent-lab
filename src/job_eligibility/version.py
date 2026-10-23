"""Eligibility policy version and prompt/schema contract hash."""

from __future__ import annotations

import hashlib
import json

from job_eligibility.prompt import SYSTEM_PROMPT
from job_eligibility.schema import EligibilityResponse

POLICY_VERSION = "cr-2026.10.1"
_SCHEMA_JSON = json.dumps(
    EligibilityResponse.model_json_schema(), sort_keys=True, separators=(",", ":")
)
PROMPT_HASH = hashlib.sha256(f"{SYSTEM_PROMPT}\n{_SCHEMA_JSON}".encode()).hexdigest()
