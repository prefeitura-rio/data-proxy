SELECT subject, unit_type, unit_id, action
FROM {{ schema }}.access_log
ORDER BY changed_at ASC
