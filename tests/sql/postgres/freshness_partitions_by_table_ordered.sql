SELECT partition, status::text FROM app.freshness WHERE "table" = %s ORDER BY partition;
