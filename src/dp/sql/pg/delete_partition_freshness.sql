DELETE FROM ${schema}.freshness
WHERE "table" = %s
  AND strategy = %s
  AND partition = %s
