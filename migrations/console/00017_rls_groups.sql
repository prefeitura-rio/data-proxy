-- +goose Up
-- noqa: disable=PRS,RF05,CP02,LT05
-- Adds console.rls.rule_group, a number that only the database reads. A rule is a
-- condition {column, unit_type} or a way {"all": [condition, ...]} that holds its
-- conditions. The database numbers the ways by their position in the list, so the same
-- ways in the same order always give the same numbers, and the configuration signature
-- stays the same when nothing changed. No function returns the number. The role of the
-- tokens loses every right on console.rls, and reads and writes the rules through
-- console.rules() and console.replace_rls(), which repeat the permission checks and the
-- schema claim of the row policies.
ALTER TABLE console.rls ADD COLUMN rule_group integer CHECK (rule_group >= 1);

ALTER TABLE console.rls ADD COLUMN sort_order integer;

UPDATE console.rls SET sort_order = id;

ALTER TABLE console.rls ALTER COLUMN sort_order SET NOT NULL;

-- +goose StatementBegin
CREATE FUNCTION console.order_rls() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    new.sort_order := coalesce(new.sort_order, new.id::integer);
    RETURN new;
END
$$;
-- +goose StatementEnd

CREATE TRIGGER order_rls BEFORE INSERT ON console.rls
FOR EACH ROW EXECUTE FUNCTION console.order_rls();

-- +goose ENVSUB ON
REVOKE ALL ON console.rls FROM "${AUTH_USER_ROLE}";
-- +goose ENVSUB OFF

-- keep_claim() reads console.rls to refuse a schema without a claim while a table has rules. The role
-- of the tokens has no right on that table, so the function runs as its owner, as require_claim() does.
ALTER FUNCTION console.keep_claim()
SECURITY DEFINER SET search_path = pg_catalog, pg_temp;

-- The ways of every table, in order, with no number: a condition, or {"all": [...]}.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION console.ways()
RETURNS TABLE (table_name text, sort_order integer, way jsonb)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, console, pg_temp AS $$
    SELECT
        r.table_name,
        min(r.sort_order) AS sort_order,
        CASE
            WHEN r.rule_group IS NULL THEN
                jsonb_build_object('column', min(r.column_name), 'unit_type', min(r.unit_type))
            ELSE
                jsonb_build_object(
                    'all',
                    jsonb_agg(
                        jsonb_build_object('column', r.column_name, 'unit_type', r.unit_type)
                        ORDER BY r.sort_order
                    )
                )
        END AS way
    FROM console.rls AS r
    GROUP BY r.table_name, coalesce(r.rule_group::text, 'alone-' || r.id::text), r.rule_group
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION console.ways() FROM PUBLIC;

-- +goose StatementBegin
CREATE OR REPLACE FUNCTION console.sync_config() RETURNS jsonb
LANGUAGE sql STABLE AS $$
    WITH ways AS (
        SELECT w.* FROM console.ways() AS w WHERE console.mine_table(w.table_name)
    ),

    rules AS (
        SELECT
            table_name,
            jsonb_agg(
                way
                ORDER BY sort_order
            ) AS rls
        FROM ways
        GROUP BY table_name
    ),

    members AS (
        SELECT
            t.schema,
            jsonb_agg(
                jsonb_build_object(
                    'name', t.name,
                    'strategy', t.strategy,
                    'n', t.n,
                    'fallback', nullif(t.fallback, FALSE),
                    'cache_ttl', t.cache_ttl,
                    'rls', r.rls,
                    'ducklake', nullif(
                        jsonb_strip_nulls(
                            jsonb_build_object(
                                'partitioning', t.partitioning, 'sort', to_jsonb(t.sort)
                            )
                        ),
                        '{}'
                    )
                )
                ORDER BY t.name
            ) AS tables
        FROM console.tables AS t
        LEFT JOIN rules AS r ON t.name = r.table_name
        GROUP BY t.schema
    )

    SELECT jsonb_build_object(
        'schemas',
        coalesce(
            jsonb_object_agg(
                s.name,
                jsonb_strip_nulls(
                    jsonb_build_object(
                        'source', jsonb_build_object('type', s.source, 'settings', s.settings),
                        'claim', s.claim,
                        'ducklake', nullif(
                            jsonb_strip_nulls(
                                jsonb_build_object('encrypted', nullif(s.encrypted, FALSE))
                            ),
                            '{}'
                        ),
                        'tables', coalesce(m.tables, '[]'::jsonb)
                    )
                )
            ),
            '{}'::jsonb
        )
    )
    FROM console.schemas AS s
    LEFT JOIN members AS m ON s.name = m.schema
$$;
-- +goose StatementEnd

-- +goose StatementBegin
CREATE OR REPLACE FUNCTION console.write_rls(table_name text, ways jsonb)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, console, pg_temp AS $$
DECLARE
    way jsonb;
    condition jsonb;
    place integer := 0;
    number integer := 0;
BEGIN
    IF jsonb_typeof(ways) <> 'array' THEN
        RAISE EXCEPTION 'The rules must be a JSON array' USING ERRCODE = 'invalid_parameter_value';
    END IF;

    DELETE FROM console.rls AS r WHERE r.table_name = write_rls.table_name;

    FOR way IN SELECT jsonb_array_elements(ways)
    LOOP
        IF way ? 'all' THEN
            IF jsonb_typeof(way -> 'all') <> 'array' OR jsonb_array_length(way -> 'all') < 2 THEN
                RAISE EXCEPTION 'A way of all needs at least two conditions'
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;

            number := number + 1;

            FOR condition IN SELECT jsonb_array_elements(way -> 'all')
            LOOP
                place := place + 1;

                INSERT INTO console.rls (table_name, column_name, unit_type, rule_group, sort_order)
                VALUES (
                    write_rls.table_name,
                    condition ->> 'column',
                    condition ->> 'unit_type',
                    number,
                    place
                );
            END LOOP;
        ELSE
            place := place + 1;

            INSERT INTO console.rls (table_name, column_name, unit_type, sort_order)
            VALUES (write_rls.table_name, way ->> 'column', way ->> 'unit_type', place);
        END IF;
    END LOOP;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION console.write_rls(text, jsonb) FROM PUBLIC;

-- +goose StatementBegin
CREATE OR REPLACE FUNCTION console.import_config(document jsonb) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT rbac.can('import_config') THEN
        RAISE EXCEPTION 'The token lacks the import_config permission'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    -- The project default exists, and a token may see no row of a project that it already uses, so add only what is new.
    INSERT INTO console.projects (name)
    SELECT p.key
    FROM jsonb_each(coalesce(document -> 'projects', '{}'::jsonb)) AS p
    WHERE
        p.key <> 'default'
        AND NOT EXISTS (SELECT FROM console.projects AS c WHERE c.name = p.key);

    INSERT INTO console.schemas (project, short_name, source, settings, claim, encrypted)
    SELECT
        s.project,
        s.short_name,
        coalesce(s.body #>> '{source,type}', 'bigquery'),
        s.body #> '{source,settings}',
        s.body ->> 'claim',
        coalesce((s.body #>> '{ducklake,encrypted}')::boolean, FALSE)
    FROM console.import_schemas(document) AS s
    ON CONFLICT (name) DO UPDATE
    SET source = excluded.source,
        settings = excluded.settings,
        claim = excluded.claim,
        encrypted = excluded.encrypted;

    -- A project claim expanded at the start of the request, so it does not have the schemas that this import added.
    PERFORM rbac.load();

    INSERT INTO console.tables (
        name, schema, strategy, n, fallback, cache_ttl, partitioning, sort
    )
    SELECT
        t.name,
        s.name,
        coalesce(t.strategy, 'full'),
        t.n,
        coalesce(t.fallback, FALSE),
        t.cache_ttl,
        t.ducklake -> 'partitioning',
        nullif(ARRAY(SELECT jsonb_array_elements_text(t.ducklake -> 'sort')), '{}')
    FROM console.import_schemas(document) AS s
    CROSS JOIN LATERAL jsonb_to_recordset(s.body -> 'tables') AS t (
        name text, strategy text, n integer, fallback boolean, cache_ttl integer, ducklake jsonb
    )
    ON CONFLICT (name) DO UPDATE
    SET strategy = excluded.strategy,
        n = excluded.n,
        fallback = excluded.fallback,
        cache_ttl = excluded.cache_ttl,
        partitioning = excluded.partitioning,
        sort = excluded.sort;

    PERFORM console.write_rls(t.name, coalesce(t.rls, '[]'::jsonb))
    FROM console.import_schemas(document) AS s
    CROSS JOIN LATERAL jsonb_to_recordset(s.body -> 'tables') AS t (name text, rls jsonb);
END
$$;
-- +goose StatementEnd


-- The history keeps what a user sets: the column and the unit type. The number of a way and the
-- order are internal, so the history row has neither.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION console.record_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    old_json jsonb := CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) - 'rule_group' - 'sort_order' END;
    new_json jsonb := CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) - 'rule_group' - 'sort_order' END;
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

-- The rules of the tables that the token may read, one row for each table, with no number.
-- The function reads past the row policies of console.rls, so it repeats them.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION console.rules()
RETURNS TABLE (table_name text, ways jsonb)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, console, pg_temp AS $$
BEGIN
    IF NOT rbac.can('read_config') THEN
        RAISE EXCEPTION 'The token lacks the read_config permission'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    RETURN QUERY
    SELECT w.table_name, jsonb_agg(w.way ORDER BY w.sort_order)
    FROM console.ways() AS w
    WHERE console.mine_table(w.table_name)
    GROUP BY w.table_name;
END
$$;
-- +goose StatementEnd

-- Replace the rules of one table with a list of conditions and ways. It runs as the owner,
-- because the token role has no right on console.rls, so it repeats the permission checks and
-- the schema claim. The table must belong to a schema of the token.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION console.replace_rls(table_name text, ways jsonb)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, console, pg_temp AS $$
BEGIN
    IF NOT (rbac.can('create_config') AND rbac.can('delete_config')) THEN
        RAISE EXCEPTION 'The token lacks the create_config and delete_config permissions'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    IF NOT console.mine_table(replace_rls.table_name) THEN
        RAISE EXCEPTION 'The table is not in a schema of the token'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    PERFORM console.write_rls(replace_rls.table_name, ways);
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION console.rules() FROM PUBLIC;
REVOKE ALL ON FUNCTION console.replace_rls(text, jsonb) FROM PUBLIC;

-- +goose ENVSUB ON
GRANT EXECUTE ON FUNCTION console.rules(), console.replace_rls(text, jsonb) TO "${AUTH_USER_ROLE}";
-- +goose ENVSUB OFF

-- +goose Down
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION console.sync_config() RETURNS jsonb
LANGUAGE sql STABLE AS $$
    WITH rules AS (
        SELECT
            table_name,
            jsonb_agg(
                jsonb_build_object('column', column_name, 'unit_type', unit_type)
                ORDER BY id
            ) AS rls
        FROM console.rls
        GROUP BY table_name
    ),

    members AS (
        SELECT
            t.schema,
            jsonb_agg(
                jsonb_build_object(
                    'name', t.name,
                    'strategy', t.strategy,
                    'n', t.n,
                    'fallback', nullif(t.fallback, FALSE),
                    'cache_ttl', t.cache_ttl,
                    'rls', r.rls,
                    'ducklake', nullif(
                        jsonb_strip_nulls(
                            jsonb_build_object(
                                'partitioning', t.partitioning, 'sort', to_jsonb(t.sort)
                            )
                        ),
                        '{}'
                    )
                )
                ORDER BY t.name
            ) AS tables
        FROM console.tables AS t
        LEFT JOIN rules AS r ON t.name = r.table_name
        GROUP BY t.schema
    )

    SELECT jsonb_build_object(
        'schemas',
        coalesce(
            jsonb_object_agg(
                s.name,
                jsonb_strip_nulls(
                    jsonb_build_object(
                        'source', jsonb_build_object('type', s.source, 'settings', s.settings),
                        'claim', s.claim,
                        'ducklake', nullif(
                            jsonb_strip_nulls(
                                jsonb_build_object('encrypted', nullif(s.encrypted, FALSE))
                            ),
                            '{}'
                        ),
                        'tables', coalesce(m.tables, '[]'::jsonb)
                    )
                )
            ),
            '{}'::jsonb
        )
    )
    FROM console.schemas AS s
    LEFT JOIN members AS m ON s.name = m.schema
$$;
-- +goose StatementEnd

-- +goose StatementBegin
CREATE OR REPLACE FUNCTION console.import_config(document jsonb) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT rbac.can('import_config') THEN
        RAISE EXCEPTION 'The token lacks the import_config permission'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    -- The project default exists, and a token may see no row of a project that it already uses, so add only what is new.
    INSERT INTO console.projects (name)
    SELECT p.key
    FROM jsonb_each(coalesce(document -> 'projects', '{}'::jsonb)) AS p
    WHERE
        p.key <> 'default'
        AND NOT EXISTS (SELECT FROM console.projects AS c WHERE c.name = p.key);

    INSERT INTO console.schemas (project, short_name, source, settings, claim, encrypted)
    SELECT
        s.project,
        s.short_name,
        coalesce(s.body #>> '{source,type}', 'bigquery'),
        s.body #> '{source,settings}',
        s.body ->> 'claim',
        coalesce((s.body #>> '{ducklake,encrypted}')::boolean, FALSE)
    FROM console.import_schemas(document) AS s
    ON CONFLICT (name) DO UPDATE
    SET source = excluded.source,
        settings = excluded.settings,
        claim = excluded.claim,
        encrypted = excluded.encrypted;

    -- A project claim expanded at the start of the request, so it does not have the schemas that this import added.
    PERFORM rbac.load();

    INSERT INTO console.tables (
        name, schema, strategy, n, fallback, cache_ttl, partitioning, sort
    )
    SELECT
        t.name,
        s.name,
        coalesce(t.strategy, 'full'),
        t.n,
        coalesce(t.fallback, FALSE),
        t.cache_ttl,
        t.ducklake -> 'partitioning',
        nullif(ARRAY(SELECT jsonb_array_elements_text(t.ducklake -> 'sort')), '{}')
    FROM console.import_schemas(document) AS s
    CROSS JOIN LATERAL jsonb_to_recordset(s.body -> 'tables') AS t (
        name text, strategy text, n integer, fallback boolean, cache_ttl integer, ducklake jsonb
    )
    ON CONFLICT (name) DO UPDATE
    SET strategy = excluded.strategy,
        n = excluded.n,
        fallback = excluded.fallback,
        cache_ttl = excluded.cache_ttl,
        partitioning = excluded.partitioning,
        sort = excluded.sort;

    DELETE FROM console.rls
    WHERE table_name IN (
        SELECT t.name
        FROM console.import_schemas(document) AS s
        CROSS JOIN LATERAL jsonb_to_recordset(s.body -> 'tables') AS t (name text)
    );

    INSERT INTO console.rls (table_name, column_name, unit_type)
    SELECT
        t.name,
        r."column",
        r.unit_type
    FROM console.import_schemas(document) AS s
    CROSS JOIN LATERAL jsonb_to_recordset(s.body -> 'tables') AS t (name text, rls jsonb)
    CROSS JOIN LATERAL jsonb_to_recordset(t.rls) AS r ("column" text, unit_type text);
END
$$;
-- +goose StatementEnd

-- +goose StatementBegin
CREATE OR REPLACE FUNCTION console.record_change() RETURNS trigger
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

ALTER FUNCTION console.keep_claim() SECURITY INVOKER RESET search_path;

DROP FUNCTION console.replace_rls(text, jsonb);

DROP FUNCTION console.rules();

DROP FUNCTION console.write_rls(text, jsonb);

DROP FUNCTION console.ways();

DROP TRIGGER order_rls ON console.rls;

DROP FUNCTION console.order_rls();

-- +goose ENVSUB ON
GRANT SELECT ON console.rls TO "${AUTH_USER_ROLE}";

GRANT INSERT (table_name, column_name, unit_type),
UPDATE (column_name, unit_type)
ON console.rls TO "${AUTH_USER_ROLE}";

GRANT DELETE ON console.rls TO "${AUTH_USER_ROLE}";
-- +goose ENVSUB OFF

ALTER TABLE console.rls DROP COLUMN sort_order;

ALTER TABLE console.rls DROP COLUMN rule_group;
