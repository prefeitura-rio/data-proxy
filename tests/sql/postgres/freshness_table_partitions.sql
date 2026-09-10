SELECT "table", partition, status::text FROM ${schema}.freshness ORDER BY "table";
