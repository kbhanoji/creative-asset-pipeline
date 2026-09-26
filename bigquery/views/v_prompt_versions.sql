-- Every prompt version per SKU, with the image versions it produced
SELECT p.sku, p.prompt_lineage_id, p.prompt_version, p.prompt_id, p.parent_prompt_id, p.origin,
       p.asset_type, p.shot_type, p.variant_index, p.resolution, p.aspect_ratio,
       p.prompt_text, p.revision_reason, p.created_by, p.created_at, p.batch_run_id,
       ARRAY_AGG(IF(g.generation_id IS NULL, NULL, STRUCT(g.generation_id, g.version, g.gcs_uri))
                 IGNORE NULLS ORDER BY g.created_at) AS generations
FROM `${project}.${dataset}.enriched_prompt` p
LEFT JOIN `${project}.${dataset}.generation` g USING (prompt_id)
GROUP BY ALL
