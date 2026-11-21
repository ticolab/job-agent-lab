"""Pure one-shot eligibility leaf with no imports from other repository packages."""

from job_eligibility.normalize import grounding_text, occurs_in
from job_eligibility.prompt import (
    INPUT_TEMPLATE,
    PROMPT_EXAMPLES,
    SYSTEM_PROMPT,
    Message,
    PromptExample,
    build_messages,
)
from job_eligibility.schema import (
    MAX_EVIDENCE_CHARACTERS,
    MAX_EVIDENCE_ITEMS,
    MAX_REASON_CHARACTERS,
    MISSING_INFORMATION_REASON,
    Decision,
    EligibilityResponse,
    EligibilityVerdict,
)
from job_eligibility.validate import (
    EligibilityValidationError,
    GroundingMiss,
    UngroundedEvidenceError,
    collect_grounding_misses,
    is_missing_information,
    normalize,
    structural_errors,
    validate,
)
from job_eligibility.version import ELIGIBILITY_PROMPT_HASH, ELIGIBILITY_VERSION

__all__ = [
    "ELIGIBILITY_PROMPT_HASH",
    "ELIGIBILITY_VERSION",
    "INPUT_TEMPLATE",
    "MAX_EVIDENCE_CHARACTERS",
    "MAX_EVIDENCE_ITEMS",
    "MAX_REASON_CHARACTERS",
    "MISSING_INFORMATION_REASON",
    "PROMPT_EXAMPLES",
    "SYSTEM_PROMPT",
    "Decision",
    "EligibilityResponse",
    "EligibilityValidationError",
    "EligibilityVerdict",
    "GroundingMiss",
    "Message",
    "PromptExample",
    "UngroundedEvidenceError",
    "build_messages",
    "collect_grounding_misses",
    "grounding_text",
    "is_missing_information",
    "normalize",
    "occurs_in",
    "structural_errors",
    "validate",
]
