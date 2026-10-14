-- +goose Up
-- noqa: disable=CP02,PRS
CREATE TABLE admin.schemas (
    name text PRIMARY KEY CHECK (name ~ '^[a-z][a-z0-9_]{0,62}$'),
    source text NOT NULL DEFAULT 'bigquery' CHECK (source <> ''),
    settings jsonb CHECK (jsonb_typeof(settings) = 'object' AND settings <> '{}'),
    claim text CHECK (claim <> ''),
    encrypted boolean NOT NULL DEFAULT FALSE,
    updated_at timestamptz NOT NULL,
    updated_by text NOT NULL
);

CREATE TABLE admin.tables (
    name text PRIMARY KEY CHECK (name <> ''),
    schema text NOT NULL REFERENCES admin.schemas (name) ON UPDATE CASCADE,
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
ON admin.tables (schema, (regexp_replace(name, '^.*\.', '')));

CREATE TABLE admin.rls (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    table_name text NOT NULL REFERENCES admin.tables (name)
    ON UPDATE CASCADE ON DELETE CASCADE,
    column_name text NOT NULL CHECK (column_name <> ''),
    unit_type text NOT NULL CHECK (unit_type <> ''),
    updated_at timestamptz NOT NULL,
    updated_by text NOT NULL,
    UNIQUE (table_name, column_name, unit_type)
);

-- +goose StatementBegin
CREATE FUNCTION admin.stamp() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    new.updated_at := now();
    new.updated_by := admin.username();
    RETURN new;
END
$$;
-- +goose StatementEnd

-- +goose StatementBegin
CREATE FUNCTION admin.require_claim() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM admin.tables AS t
        INNER JOIN admin.schemas AS s ON t.schema = s.name
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
CREATE FUNCTION admin.keep_claim() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM admin.rls AS r
        INNER JOIN admin.tables AS t ON r.table_name = t.name
        WHERE t.schema = new.name
    ) THEN
        RAISE EXCEPTION 'Schema % has rls rules, so it needs a claim', new.name
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN new;
END
$$;
-- +goose StatementEnd

CREATE TRIGGER stamp BEFORE INSERT OR UPDATE ON admin.schemas
FOR EACH ROW EXECUTE FUNCTION admin.stamp();
CREATE TRIGGER stamp BEFORE INSERT OR UPDATE ON admin.tables
FOR EACH ROW EXECUTE FUNCTION admin.stamp();
CREATE TRIGGER stamp BEFORE INSERT OR UPDATE ON admin.rls
FOR EACH ROW EXECUTE FUNCTION admin.stamp();

CREATE TRIGGER require_claim BEFORE INSERT OR UPDATE ON admin.rls
FOR EACH ROW EXECUTE FUNCTION admin.require_claim();

CREATE TRIGGER keep_claim BEFORE UPDATE OF claim ON admin.schemas
FOR EACH ROW WHEN (new.claim IS NULL) EXECUTE FUNCTION admin.keep_claim();

REVOKE ALL ON FUNCTION admin.stamp() FROM PUBLIC;
REVOKE ALL ON FUNCTION admin.require_claim() FROM PUBLIC;
REVOKE ALL ON FUNCTION admin.keep_claim() FROM PUBLIC;

GRANT INSERT (name, source, settings, claim, encrypted),
UPDATE (source, settings, claim, encrypted)
ON admin.schemas TO console_editor;

GRANT INSERT (name, schema, strategy, n, fallback, cache_ttl, partitioning, sort),
UPDATE (strategy, n, fallback, cache_ttl, partitioning, sort)
ON admin.tables TO console_editor;

GRANT INSERT (table_name, column_name, unit_type),
UPDATE (column_name, unit_type)
ON admin.rls TO console_editor;

GRANT DELETE ON admin.schemas TO console_admin;
GRANT DELETE ON admin.tables TO console_admin;
GRANT DELETE ON admin.rls TO console_admin;

-- +goose Down
DROP TABLE admin.rls;
DROP TABLE admin.tables;
DROP TABLE admin.schemas;
DROP FUNCTION admin.keep_claim();
DROP FUNCTION admin.require_claim();
DROP FUNCTION admin.stamp();
