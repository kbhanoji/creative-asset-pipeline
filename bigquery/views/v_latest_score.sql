-- Latest score per image version
SELECT * EXCEPT(rn) FROM (
  SELECT s.*, ROW_NUMBER() OVER (PARTITION BY generation_id, version ORDER BY scored_at DESC) AS rn
  FROM `${project}.${dataset}.score` s)
WHERE rn = 1
