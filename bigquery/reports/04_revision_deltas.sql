-- Score change from each HITL revision (SOW acceptance 5.a: pre vs post edit deviation)
WITH s AS (SELECT generation_id, version, composite, hallucination_risk FROM `${project}.${dataset}.v_latest_score`)
SELECT e.generation_id, e.prev_version, e.new_version, e.editor_identity, e.prompt_text,
       a.composite AS before_score, b.composite AS after_score, b.composite - a.composite AS delta,
       e.created_at
FROM `${project}.${dataset}.edit_event` e
JOIN s a ON a.generation_id = e.generation_id AND a.version = e.prev_version
JOIN s b ON b.generation_id = e.generation_id AND b.version = e.new_version
WHERE e.action = 'REGENERATE'
ORDER BY e.created_at
