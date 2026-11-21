"""Pure one-shot eligibility leaf with no imports from other repository packages."""

from job_eligibility.normalize import (
    contains_words,
    locate,
    occurs_in,
    word_key,
    words,
)
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
    structural_errors,
    validate,
)
from job_eligibility.version import ELIGIBILITY_PROMPT_HASH

__all__ = [
    "ELIGIBILITY_PROMPT_HASH",
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
    "contains_words",
    "is_missing_information",
    "locate",
    "occurs_in",
    "structural_errors",
    "validate",
    "word_key",
    "words",
]
