SELECT
    'dispositions_per_company' AS report,
    company_slug,
    status,
    COUNT(*) AS jobs
FROM jobs
GROUP BY company_slug, status
ORDER BY company_slug, status;

SELECT
    'jobs_with_multiple_attempts' AS report,
    j.id AS job_id,
    j.company_slug,
    j.status,
    COUNT(a.id) AS attempts
FROM jobs AS j
JOIN job_attempts AS a ON a.job_id = j.id
GROUP BY j.id, j.company_slug, j.status
HAVING COUNT(a.id) > 1
ORDER BY attempts DESC, j.company_slug, j.id;

SELECT DISTINCT
    'current_results_with_failed_fields' AS report,
    j.id AS job_id,
    j.company_slug,
    tree.fullkey AS failed_field
FROM job_results AS r
JOIN jobs AS j ON j.id = r.job_id
JOIN json_tree(r.result_json) AS tree
WHERE r.is_current = 1
  AND tree.key = 'state'
  AND tree.value = 'failed'
ORDER BY j.company_slug, j.id, tree.fullkey;

CREATE TEMP TABLE IF NOT EXISTS pipeline_model_prices (
    model TEXT PRIMARY KEY,
    input_usd_per_million REAL NOT NULL,
    output_usd_per_million REAL NOT NULL
);

WITH usage_groups AS (
    SELECT
        'run' AS cost_scope,
        a.run_id AS scope_value,
        a.model,
        COUNT(*) AS attempts,
        SUM(COALESCE(a.prompt_tokens, 0)) AS prompt_tokens,
        SUM(COALESCE(a.completion_tokens, 0)) AS completion_tokens
    FROM job_attempts AS a
    GROUP BY a.run_id, a.model
    UNION ALL
    SELECT
        'outcome' AS cost_scope,
        COALESCE(a.outcome, 'in_progress') AS scope_value,
        a.model,
        COUNT(*) AS attempts,
        SUM(COALESCE(a.prompt_tokens, 0)) AS prompt_tokens,
        SUM(COALESCE(a.completion_tokens, 0)) AS completion_tokens
    FROM job_attempts AS a
    GROUP BY COALESCE(a.outcome, 'in_progress'), a.model
)
SELECT
    'usage_and_cost' AS report,
    u.cost_scope,
    u.scope_value,
    u.model,
    u.attempts,
    u.prompt_tokens,
    u.completion_tokens,
    p.input_usd_per_million,
    p.output_usd_per_million,
    CASE
        WHEN p.model IS NULL THEN NULL
        ELSE ROUND(
            (
                u.prompt_tokens * p.input_usd_per_million
                + u.completion_tokens * p.output_usd_per_million
            ) / 1000000.0,
            6
        )
    END AS estimated_cost_usd
FROM usage_groups AS u
LEFT JOIN pipeline_model_prices AS p ON p.model = u.model
ORDER BY u.cost_scope, u.scope_value, u.model;

SELECT
    'unknown_decisions_for_policy_review' AS report,
    j.id AS job_id,
    j.company_slug,
    d.reason,
    d.policy_version,
    d.decided_at
FROM job_decisions AS d
JOIN jobs AS j ON j.id = d.job_id
WHERE d.is_current = 1
  AND d.decision = 'unknown'
ORDER BY d.decided_at, j.company_slug, j.id;
