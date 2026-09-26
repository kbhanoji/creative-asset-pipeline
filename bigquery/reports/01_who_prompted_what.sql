-- Who prompted what, which images each prompt version produced, and where they ended up
SELECT prompted_by, sku, prompt_id, prompt_version, generation_id, version,
       composite, current_status, output_stage, intermediate_uri, status_uri, created_at
FROM `${project}.${dataset}.v_asset_lifecycle`
WHERE (@user = '' OR prompted_by = @user)
  AND (@batch_run_id = '' OR batch_run_id = @batch_run_id)
ORDER BY created_at
