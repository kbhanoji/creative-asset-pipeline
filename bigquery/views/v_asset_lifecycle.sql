-- One row per image version: who prompted it, prompt used, scores, current status, intermediate vs final
WITH gen AS (
  SELECT g.*, MAX(g.version) OVER (PARTITION BY g.generation_id) AS latest_version
  FROM `${project}.${dataset}.generation` g)
SELECT g.batch_run_id, g.sku, g.parent_asset_id, g.generation_id, g.version, g.parent_generation_id,
       g.prompt_id, p.prompt_lineage_id, p.prompt_version,
       p.created_by AS prompted_by, g.created_by AS generated_by, g.tool, g.generation_mode, g.match_method,
       g.gcs_uri AS intermediate_uri,
       s.composite, s.hallucination_risk, s.hallucination_band, s.tier, s.rationale,
       st.status AS current_status, st.is_final, st.gcs_uri AS status_uri, st.set_by AS status_set_by,
       CASE
         WHEN g.version < g.latest_version THEN 'INTERMEDIATE_SUPERSEDED'
         WHEN st.status = 'APPROVED' THEN 'FINAL_APPROVED'
         WHEN st.status IN ('FAILED_QC', 'REJECTED') THEN 'FINAL_REJECTED'
         ELSE 'INTERMEDIATE_IN_PROGRESS'
       END AS output_stage,
       g.created_at
FROM gen g
LEFT JOIN `${project}.${dataset}.enriched_prompt` p USING (prompt_id)
LEFT JOIN `${project}.${dataset}.v_latest_score` s USING (generation_id, version)
LEFT JOIN `${project}.${dataset}.v_current_status` st
       ON st.entity_type = 'GENERATION' AND st.entity_id = g.generation_id AND st.version = g.version
