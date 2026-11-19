-- +goose Up
-- noqa: disable=PRS,RF05,CP02
-- Adds console.rls.rule_group. Rules with the same group all must match, and a rule
-- with no group is an alternative, as before. sync_config() writes the group as
-- `group` and omits it when it is NULL, so a table with no group has the same
-- document as before. import_config() reads it back. replace_rls() replaces the rules
-- of one table in one statement, so a half-saved group never reaches the sync.
-- The UNIQUE key keeps (table, column, unit type), so a column has one rule for each
-- unit type.
ALTER TABLE console.rls ADD COLUMN rule_group integer CHECK (rule_group >= 1);

-- +goose ENVSUB ON
GRANT INSERT (rule_group), UPDATE (rule_group) ON console.rls TO "${AUTH_USER_ROLE}";
-- +goose ENVSUB OFF

-- +goose StatementBegin
CREATE OR REPLACE FUNCTION console.sync_config() RETURNS jsonb
LANGUAGE sql STABLE AS $$
    WITH rules AS (
        SELECT
            table_name,
            jsonb_agg(
                jsonb_strip_nulls(
                    jsonb_build_object(
                        'column', column_name, 'unit_type', unit_type, 'group', rule_group
                    )
                )
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

    INSERT INTO console.rls (table_name, column_name, unit_type, rule_group)
    SELECT
        t.name,
        r."column",
        r.unit_type,
        r."group"
    FROM console.import_schemas(document) AS s
    CROSS JOIN LATERAL jsonb_to_recordset(s.body -> 'tables') AS t (name text, rls jsonb)
    CROSS JOIN LATERAL jsonb_to_recordset(t.rls) AS r (
        "column" text, unit_type text, "group" integer
    );
END
$$;
-- +goose StatementEnd

-- Replace the rules of one table with a list of {column, unit_type, group} objects.
-- It runs as the caller, so the row policies of console.rls decide who writes which
-- table. The permission checks stay the same as for a plain insert and delete.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION console.replace_rls(table_name text, rules jsonb)
RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT (rbac.can('create_config') AND rbac.can('delete_config')) THEN
        RAISE EXCEPTION 'The token lacks the create_config and delete_config permissions'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    IF jsonb_typeof(rules) <> 'array' THEN
        RAISE EXCEPTION 'The rules must be a JSON array' USING ERRCODE = 'invalid_parameter_value';
    END IF;

    DELETE FROM console.rls AS r WHERE r.table_name = replace_rls.table_name;

    INSERT INTO console.rls (table_name, column_name, unit_type, rule_group)
    SELECT replace_rls.table_name, x."column", x.unit_type, x."group"
    FROM jsonb_to_recordset(rules) AS x ("column" text, unit_type text, "group" integer);
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION console.replace_rls(text, jsonb) FROM PUBLIC;

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

DROP FUNCTION console.replace_rls(text, jsonb);

ALTER TABLE console.rls DROP COLUMN rule_group;
