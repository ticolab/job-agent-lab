from __future__ import annotations

import hashlib
import json

from job_extractor.prompt import SYSTEM_PROMPT
from job_extractor.schema import ExtractionResponse

EXTRACTOR_VERSION = "2026.10.1"
_SCHEMA_JSON = json.dumps(
    ExtractionResponse.model_json_schema(), sort_keys=True, separators=(",", ":")
)
PROMPT_HASH = hashlib.sha256((SYSTEM_PROMPT + _SCHEMA_JSON).encode()).hexdigest()
