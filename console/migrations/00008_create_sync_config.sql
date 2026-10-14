-- +goose Up
-- noqa: disable=CP02,PRS
-- +goose StatementBegin
CREATE FUNCTION admin.sync_config() RETURNS jsonb
LANGUAGE sql STABLE AS $$
    WITH rules AS (
        SELECT
            table_name,
            jsonb_agg(
                jsonb_build_object('column', column_name, 'unit_type', unit_type)
                ORDER BY id
            ) AS rls
        FROM admin.rls
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
        FROM admin.tables AS t
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
    FROM admin.schemas AS s
    LEFT JOIN members AS m ON s.name = m.schema
$$;
-- +goose StatementEnd

-- +goose StatementBegin
CREATE FUNCTION admin.import_config(document jsonb) RETURNS void
LANGUAGE sql AS $$
    INSERT INTO admin.schemas (name, source, settings, claim, encrypted)
    SELECT
        s.key,
        coalesce(s.value #>> '{source,type}', 'bigquery'),
        s.value #> '{source,settings}',
        s.value ->> 'claim',
        coalesce((s.value #>> '{ducklake,encrypted}')::boolean, FALSE)
    FROM jsonb_each(document -> 'schemas') AS s
    ON CONFLICT (name) DO UPDATE
    SET source = excluded.source,
        settings = excluded.settings,
        claim = excluded.claim,
        encrypted = excluded.encrypted;

    INSERT INTO admin.tables (
        name, schema, strategy, n, fallback, cache_ttl, partitioning, sort
    )
    SELECT
        t.name,
        s.key,
        coalesce(t.strategy, 'full'),
        t.n,
        coalesce(t.fallback, FALSE),
        t.cache_ttl,
        t.ducklake -> 'partitioning',
        nullif(ARRAY(SELECT jsonb_array_elements_text(t.ducklake -> 'sort')), '{}')
    FROM jsonb_each(document -> 'schemas') AS s
    CROSS JOIN LATERAL jsonb_to_recordset(s.value -> 'tables') AS t (
        name text, strategy text, n integer, fallback boolean, cache_ttl integer, ducklake jsonb
    )
    ON CONFLICT (name) DO UPDATE
    SET strategy = excluded.strategy,
        n = excluded.n,
        fallback = excluded.fallback,
        cache_ttl = excluded.cache_ttl,
        partitioning = excluded.partitioning,
        sort = excluded.sort;

    DELETE FROM admin.rls
    WHERE table_name IN (
        SELECT t.name
        FROM jsonb_each(document -> 'schemas') AS s
        CROSS JOIN LATERAL jsonb_to_recordset(s.value -> 'tables') AS t (name text)
    );

    INSERT INTO admin.rls (table_name, column_name, unit_type)
    SELECT
        t.name,
        r."column",
        r.unit_type
    FROM jsonb_each(document -> 'schemas') AS s
    CROSS JOIN LATERAL jsonb_to_recordset(s.value -> 'tables') AS t (name text, rls jsonb)
    CROSS JOIN LATERAL jsonb_to_recordset(t.rls) AS r ("column" text, unit_type text);
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION admin.sync_config() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION admin.sync_config() TO console_viewer;

REVOKE ALL ON FUNCTION admin.import_config(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION admin.import_config(jsonb) TO console_admin;

-- +goose Down
DROP FUNCTION admin.import_config(jsonb);
DROP FUNCTION admin.sync_config();
