INSERT INTO ${schema}.freshness
    ("table", strategy, partition, updated_at, attempted_at, status)
VALUES (%s, %s, %s, %s, %s, %s::rls.sync_status)
ON CONFLICT ("table", strategy, partition) DO UPDATE SET
    updated_at = CASE
        WHEN EXCLUDED.status = 'success'::rls.sync_status
        THEN EXCLUDED.updated_at
        ELSE freshness.updated_at
    END,
    attempted_at = EXCLUDED.attempted_at,
    status = EXCLUDED.status
