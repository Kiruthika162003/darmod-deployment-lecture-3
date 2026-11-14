-- One row per Maple chat turn, flattened from the log lines the sink copies into BigQuery.
-- Replace PROJECT_ID before running. Looker Studio reads this view.
CREATE OR REPLACE VIEW `PROJECT_ID.maple_ops.turns` AS
SELECT
  timestamp,
  jsonPayload.prompt_version AS prompt_version,
  jsonPayload.revision AS revision,
  jsonPayload.commit AS `commit`,
  jsonPayload.model AS model,
  jsonPayload.session_id AS session_id,
  jsonPayload.latency_ms AS latency_ms,
  jsonPayload.grounded AS grounded,
  jsonPayload.search_allowed AS search_allowed,
  jsonPayload.input_tokens AS input_tokens,
  jsonPayload.output_tokens AS output_tokens,
  jsonPayload.answer_chars AS answer_chars,
  jsonPayload.cold_start AS cold_start
FROM `PROJECT_ID.maple_ops.run_googleapis_com_stdout`
WHERE jsonPayload.event = 'maple.turn';
