"""One-shot eligibility contract version and prompt/schema hash.

``ELIGIBILITY_VERSION`` names the semantic contract: the embedded
operator-approved opportunity-screening-v2 policy, the six synthetic prompt
examples, the four-field response shape, structural rules, and grounding
normalization.
Any semantic change bumps it and requires model-backed reprocessing, because no
durable facts exist to re-screen without a model call.

``ELIGIBILITY_PROMPT_HASH`` changes whenever the prompt, input wrapper, or
response-schema bytes change. Decision reuse, recordings, and evaluator replay
require both values to match.
"""

from __future__ import annotations

import hashlib
import json

from job_eligibility.prompt import INPUT_TEMPLATE, SYSTEM_PROMPT
from job_eligibility.schema import EligibilityResponse

ELIGIBILITY_VERSION = "opportunity-oneshot-v2"
_SCHEMA_JSON = json.dumps(
    EligibilityResponse.model_json_schema(), sort_keys=True, separators=(",", ":")
)
ELIGIBILITY_PROMPT_HASH = hashlib.sha256(
    f"{SYSTEM_PROMPT}\n{INPUT_TEMPLATE}\n{_SCHEMA_JSON}".encode()
).hexdigest()
