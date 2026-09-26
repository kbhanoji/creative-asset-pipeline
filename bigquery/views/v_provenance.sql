-- SOW 3.4.2 parent-child rule: every derivative of a parent asset, all versions, scores, edits, status, asset groups
WITH edits AS (
  SELECT generation_id, IFNULL(new_version, prev_version) AS version,
         ARRAY_AGG(STRUCT(event_id, action, editor_identity, prev_version, new_version, prompt_text, created_at)
                   ORDER BY created_at) AS edits
  FROM `${project}.${dataset}.edit_event`
  GROUP BY 1, 2),
gen_asset_groups AS (
  SELECT source_generation_id AS generation_id,
         ARRAY_AGG(STRUCT(ag.asset_group_id, ag.group_type, ag.gcs_uri)) AS asset_groups
  FROM `${project}.${dataset}.asset_group` ag, UNNEST(ag.source_generation_ids) AS source_generation_id
  GROUP BY 1)
SELECT l.parent_asset_id, l.sku, l.generation_id, l.version, l.prompt_id, l.prompted_by, l.generated_by,
       l.intermediate_uri, l.composite, l.hallucination_risk, l.tier, l.current_status, l.output_stage,
       e.edits, g.asset_groups
FROM `${project}.${dataset}.v_asset_lifecycle` l
LEFT JOIN edits e USING (generation_id, version)
LEFT JOIN gen_asset_groups g USING (generation_id)
