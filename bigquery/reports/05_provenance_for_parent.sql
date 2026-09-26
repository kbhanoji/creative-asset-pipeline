-- Full provenance for one source asset (SOW 3.4.2 question)
SELECT * FROM `${project}.${dataset}.v_provenance`
WHERE parent_asset_id = @parent_asset_id
ORDER BY generation_id, version
