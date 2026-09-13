COPY (
    SELECT * FROM read_parquet(${scratch})
) TO ${path} (FORMAT PARQUET)
