# Historical Prompts

The four `stage_*.md` files are historical product evidence. They preserve the rules and vocabulary that informed the replacement design, but they are not runtime contracts.

Eligibility now lives in the pure `job_eligibility` leaf. Structured extraction and code-owned technology normalization live in the pure `job_extractor` leaf. Their prompts, schemas, validators, versions, and tests supersede these files.

Do not edit a historical prompt to change runtime behavior. Change the appropriate leaf, bump its version when its prompt or response schema changes, and update the prompt-hash golden test.
