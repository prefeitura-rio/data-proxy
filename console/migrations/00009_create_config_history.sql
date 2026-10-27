-- +goose Up
-- noqa: disable=CP02,PRS
CREATE TABLE console.config_history (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    changed_at timestamptz NOT NULL DEFAULT now(),
    changed_by text,
    action text NOT NULL CHECK (action IN ('insert', 'update', 'delete')),
    object text NOT NULL CHECK (object IN ('schemas', 'tables', 'rls')),
    schema_name text NOT NULL,
    key text NOT NULL,
    old_row jsonb,
    new_row jsonb
);

CREATE INDEX config_history_by_time
ON console.config_history (changed_at DESC, id DESC);

ALTER TABLE console.config_history ENABLE ROW LEVEL SECURITY;

CREATE POLICY read_scope ON console.config_history FOR SELECT
USING (rbac.can('read_config') AND rbac.mine(schema_name));

-- +goose StatementBegin
CREATE FUNCTION console.record_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    old_json jsonb := CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END;
    new_json jsonb := CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END;
    changed jsonb := coalesce(new_json, old_json);
    in_schema text;
BEGIN
    in_schema := CASE TG_TABLE_NAME
        WHEN 'schemas' THEN changed ->> 'name'
        WHEN 'tables' THEN changed ->> 'schema'
        ELSE (
            SELECT t.schema FROM console.tables AS t WHERE t.name = changed ->> 'table_name'
        )
    END;

    IF in_schema IS NULL THEN
        RETURN NULL;
    END IF;

    INSERT INTO console.config_history (
        changed_by, action, object, schema_name, key, old_row, new_row
    )
    VALUES (
        console.username(),
        lower(TG_OP),
        TG_TABLE_NAME,
        in_schema,
        coalesce(changed ->> 'name', (changed ->> 'table_name') || '.' || (changed ->> 'column_name')),
        old_json,
        new_json
    );

    RETURN NULL;
END
$$;
-- +goose StatementEnd

CREATE TRIGGER record_change
AFTER INSERT OR UPDATE OR DELETE ON console.schemas
FOR EACH ROW EXECUTE FUNCTION console.record_change();
CREATE TRIGGER record_change
AFTER INSERT OR UPDATE OR DELETE ON console.tables
FOR EACH ROW EXECUTE FUNCTION console.record_change();
CREATE TRIGGER record_change
AFTER INSERT OR UPDATE OR DELETE ON console.rls
FOR EACH ROW EXECUTE FUNCTION console.record_change();

-- +goose Down
DROP TRIGGER record_change ON console.rls;
DROP TRIGGER record_change ON console.tables;
DROP TRIGGER record_change ON console.schemas;
DROP FUNCTION console.record_change();
DROP TABLE console.config_history;
