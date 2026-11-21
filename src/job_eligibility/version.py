"""Automatic fingerprint of the eligibility prompt and response schema.

``ELIGIBILITY_PROMPT_HASH`` changes whenever the prompt, input wrapper, or
response-schema bytes change. Nothing sets it by hand. Each stored decision
records it, and evaluation recordings are tied to it, so answers produced by one
prompt are never scored or reused as if another prompt had produced them.
"""

from __future__ import annotations

import hashlib
import json

from job_eligibility.prompt import INPUT_TEMPLATE, SYSTEM_PROMPT
from job_eligibility.schema import EligibilityResponse

_SCHEMA_JSON = json.dumps(
    EligibilityResponse.model_json_schema(), sort_keys=True, separators=(",", ":")
)
ELIGIBILITY_PROMPT_HASH = hashlib.sha256(
    f"{SYSTEM_PROMPT}\n{INPUT_TEMPLATE}\n{_SCHEMA_JSON}".encode()
).hexdigest()
