CREATE TABLE ${schema}.freshness (
    "table" text NOT NULL,
    strategy text NOT NULL,
    partition text,
    updated_at timestamptz,
    attempted_at timestamptz NOT NULL,
    status rls.sync_status NOT NULL,
    UNIQUE NULLS NOT DISTINCT ("table", strategy, partition)
)
