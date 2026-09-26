-- Image counts per user: intermediate vs final
SELECT user, SUM(prompt_versions) AS prompt_versions, SUM(images_total) AS images_total,
       SUM(images_intermediate) AS intermediate, SUM(images_final_approved) AS final_approved,
       SUM(images_final_rejected) AS final_rejected
FROM `${project}.${dataset}.v_user_activity`
GROUP BY user ORDER BY images_total DESC
