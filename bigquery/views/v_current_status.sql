-- Current status of every entity (latest disposition row wins)
SELECT * EXCEPT(rn) FROM (
  SELECT d.*, ROW_NUMBER() OVER (PARTITION BY entity_type, entity_id, IFNULL(version, 0) ORDER BY created_at DESC) AS rn
  FROM `${project}.${dataset}.disposition` d)
WHERE rn = 1
