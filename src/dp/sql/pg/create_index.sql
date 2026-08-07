CREATE INDEX CONCURRENTLY IF NOT EXISTS ${name}
    ON ${schema}.${table} (${columns})
