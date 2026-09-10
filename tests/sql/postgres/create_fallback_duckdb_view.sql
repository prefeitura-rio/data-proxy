SELECT duckdb.raw_query('CREATE OR REPLACE VIEW bq_fallback_${schema}_t AS SELECT 7::BIGINT AS id, DATE ''2024-01-02'' AS born, true AS active, ''{"x": 1}'' AS data, 9::BIGINT AS "select"')
