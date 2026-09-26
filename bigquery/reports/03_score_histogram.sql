-- Score histogram (SOW acceptance 3.a), 5-point buckets
SELECT CAST(FLOOR(composite / 5) * 5 AS INT64) AS bucket_from, COUNT(*) AS images,
       COUNTIF(decision_status = 'APPROVED') AS approved,
       COUNTIF(decision_status = 'NEEDS_REVISION') AS needs_revision,
       COUNTIF(decision_status IN ('FAILED_QC', 'FLAGGED')) AS failed_or_flagged
FROM `${project}.${dataset}.score`
GROUP BY 1 ORDER BY 1
