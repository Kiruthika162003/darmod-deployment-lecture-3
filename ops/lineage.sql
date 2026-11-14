-- Which build answered? Replace PROJECT_ID before running.

-- 1. The last 20 answers, each tied to the prompt, revision and commit that produced it.
SELECT
  timestamp, prompt_version, revision, `commit`, grounded, latency_ms, output_tokens
FROM `PROJECT_ID.maple_ops.turns`
ORDER BY timestamp DESC
LIMIT 20;

-- 2. The same data by version: what changed when v2.4 took over?
SELECT
  prompt_version,
  revision,
  COUNT(*) AS answers,
  ROUND(AVG(IF(grounded, 1, 0)) * 100) AS pct_searched,
  CAST(APPROX_QUANTILES(latency_ms, 100)[OFFSET(50)] AS INT64) AS median_latency_ms,
  CAST(APPROX_QUANTILES(output_tokens, 100)[OFFSET(50)] AS INT64) AS median_output_tokens
FROM `PROJECT_ID.maple_ops.turns`
WHERE search_allowed
GROUP BY prompt_version, revision
ORDER BY MIN(timestamp);
