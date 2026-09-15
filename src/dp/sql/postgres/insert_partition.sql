CREATE TEMP TABLE ${temp} AS
SELECT ${cols}
FROM read_parquet(${path}) AS r
WHERE ${predicate};
INSERT INTO ${schema}.${table} SELECT * FROM ${temp} ON CONFLICT DO NOTHING;
DROP TABLE ${temp}
