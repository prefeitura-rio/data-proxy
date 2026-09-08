CREATE TEMP TABLE ${temp} AS SELECT ${cols} FROM read_parquet(${path}) AS r;
INSERT INTO ${schema}.${table} SELECT * FROM ${temp};
DROP TABLE ${temp}
