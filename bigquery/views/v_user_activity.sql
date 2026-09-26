-- Who prompted what and how many images (intermediate and final) came out of it
SELECT p.created_by AS user, p.batch_run_id, p.sku,
       COUNT(DISTINCT p.prompt_id) AS prompt_versions,
       COUNT(DISTINCT p.prompt_lineage_id) AS prompt_families,
       COUNT(DISTINCT CONCAT(l.generation_id, ':', CAST(l.version AS STRING))) AS images_total,
       COUNTIF(l.output_stage LIKE 'INTERMEDIATE%') AS images_intermediate,
       COUNTIF(l.output_stage = 'FINAL_APPROVED') AS images_final_approved,
       COUNTIF(l.output_stage = 'FINAL_REJECTED') AS images_final_rejected,
       MIN(p.created_at) AS first_prompt_at, MAX(p.created_at) AS last_prompt_at
FROM `${project}.${dataset}.enriched_prompt` p
LEFT JOIN `${project}.${dataset}.v_asset_lifecycle` l USING (prompt_id)
GROUP BY 1, 2, 3
