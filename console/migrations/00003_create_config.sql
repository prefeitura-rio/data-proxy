-- +goose Up
-- noqa: disable=CP02,PRS
CREATE TABLE console.schemas (
    name text PRIMARY KEY CHECK (name ~ '^[a-z][a-z0-9_]{0,62}$'),
    source text NOT NULL DEFAULT 'bigquery' CHECK (source <> ''),
    settings jsonb CHECK (jsonb_typeof(settings) = 'object' AND settings <> '{}'),
    claim text CHECK (claim <> ''),
    encrypted boolean NOT NULL DEFAULT FALSE,
    updated_at timestamptz NOT NULL,
    updated_by text NOT NULL,
    CONSTRAINT reserved_name CHECK (
        name !~ '^(console|rbac|data_proxy|dbos|public|duckdb|information_schema)$'
        AND name !~ '^pg_'
    )
);

CREATE TABLE console.tables (
    name text PRIMARY KEY CHECK (name <> ''),
    schema text NOT NULL REFERENCES console.schemas (name) ON UPDATE CASCADE,
    strategy text NOT NULL DEFAULT 'full' CHECK (strategy IN ('full', 'partitioned')),
    n integer CHECK (n > 0),
    fallback boolean NOT NULL DEFAULT FALSE,
    cache_ttl integer CHECK (cache_ttl >= 0),
    partitioning jsonb CHECK (jsonb_typeof(partitioning) = 'array'),
    sort text[] CHECK (cardinality(sort) > 0),
    updated_at timestamptz NOT NULL,
    updated_by text NOT NULL,
    CHECK (strategy = 'partitioned' OR (n IS NULL AND NOT fallback))
);

CREATE UNIQUE INDEX tables_destination
ON console.tables (schema, (regexp_replace(name, '^.*\.', '')));

CREATE TABLE console.rls (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    table_name text NOT NULL REFERENCES console.tables (name)
    ON UPDATE CASCADE ON DELETE CASCADE,
    column_name text NOT NULL CHECK (column_name <> ''),
    unit_type text NOT NULL CHECK (unit_type <> ''),
    updated_at timestamptz NOT NULL,
    updated_by text NOT NULL,
    UNIQUE (table_name, column_name, unit_type)
);

-- +goose StatementBegin
CREATE FUNCTION console.stamp() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    new.updated_at := now();
    new.updated_by := console.username();
    RETURN new;
END
$$;
-- +goose StatementEnd

-- +goose StatementBegin
CREATE FUNCTION console.require_claim() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM console.tables AS t
        INNER JOIN console.schemas AS s ON t.schema = s.name
        WHERE t.name = new.table_name AND s.claim IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'The schema of table % has no claim, so it can''t have rls rules',
            new.table_name USING ERRCODE = 'check_violation';
    END IF;
    RETURN new;
END
$$;
-- +goose StatementEnd

-- +goose StatementBegin
CREATE FUNCTION console.keep_claim() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM console.rls AS r
        INNER JOIN console.tables AS t ON r.table_name = t.name
        WHERE t.schema = new.name
    ) THEN
        RAISE EXCEPTION 'Schema % has rls rules, so it needs a claim', new.name
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN new;
END
$$;
-- +goose StatementEnd

CREATE TRIGGER stamp BEFORE INSERT OR UPDATE ON console.schemas
FOR EACH ROW EXECUTE FUNCTION console.stamp();
CREATE TRIGGER stamp BEFORE INSERT OR UPDATE ON console.tables
FOR EACH ROW EXECUTE FUNCTION console.stamp();
CREATE TRIGGER stamp BEFORE INSERT OR UPDATE ON console.rls
FOR EACH ROW EXECUTE FUNCTION console.stamp();

CREATE TRIGGER require_claim BEFORE INSERT OR UPDATE ON console.rls
FOR EACH ROW EXECUTE FUNCTION console.require_claim();

CREATE TRIGGER keep_claim BEFORE UPDATE OF claim ON console.schemas
FOR EACH ROW WHEN (new.claim IS NULL) EXECUTE FUNCTION console.keep_claim();

REVOKE ALL ON FUNCTION console.stamp() FROM PUBLIC;
REVOKE ALL ON FUNCTION console.require_claim() FROM PUBLIC;
REVOKE ALL ON FUNCTION console.keep_claim() FROM PUBLIC;

-- +goose Down
DROP TABLE console.rls;
DROP TABLE console.tables;
DROP TABLE console.schemas;
DROP FUNCTION console.keep_claim();
DROP FUNCTION console.require_claim();
DROP FUNCTION console.stamp();
