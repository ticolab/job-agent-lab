# Job Pipeline Follow-ups

These items are deliberate v1 omissions. They are not evidence that a live rollout or model comparison has happened.

## Recheck completed jobs

Add a `recheck` command that re-fetches completed jobs on a slow cadence. It should detect closed postings and URL reuse without making ordinary daily runs pay for completed work.

## Cross-company duplicate detection

Compare content fingerprints across company slugs while preserving each source and company attribution. V1 only merges requisition or fingerprint matches within one company.

## Structured compensation

Parse explicit compensation into currency, amount or range, period, and source wording. Until that contract is approved, extraction retains grounded compensation text.

## Dashboard read model

Build a query-oriented read model only after a consumer defines its filters, latency needs, and retention expectations. The normalized history remains the source of truth.

## Version-triggered bulk reprocessing

Add auditable selection by policy, extractor, or schema version if a future policy change justifies bulk reprocessing. Version changes do not trigger reprocessing in v1.
