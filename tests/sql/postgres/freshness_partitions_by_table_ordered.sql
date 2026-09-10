SELECT partition, status::text FROM ${schema}.freshness WHERE "table" = %s ORDER BY partition;
