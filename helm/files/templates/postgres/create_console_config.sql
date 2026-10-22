{#
{
  "kind": "template",
  "description": "Create the functions that read and write the sync configuration as one document: the full document for the sync worker, the import of a document, the preview of what an import would change, and the short document that the proxy reads. The short document carries the counter in rbac.revision, so the proxy sees a change of the rules or of the access policy as a change of the document. They read and write the configuration tables of the console.",
  "inputs": {}
}
#}
-- noqa: disable=CP02,PRS
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

CREATE OR REPLACE FUNCTION console.import_config(document jsonb) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT rbac.can('import_config') THEN
        RAISE EXCEPTION 'The token lacks the import_config permission'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    INSERT INTO console.schemas (name, source, settings, claim, encrypted)
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

    INSERT INTO console.tables (
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

    DELETE FROM console.rls
    WHERE table_name IN (
        SELECT t.name
        FROM jsonb_each(document -> 'schemas') AS s
        CROSS JOIN LATERAL jsonb_to_recordset(s.value -> 'tables') AS t (name text)
    );

    INSERT INTO console.rls (table_name, column_name, unit_type)
    SELECT
        t.name,
        r."column",
        r.unit_type
    FROM jsonb_each(document -> 'schemas') AS s
    CROSS JOIN LATERAL jsonb_to_recordset(s.value -> 'tables') AS t (name text, rls jsonb)
    CROSS JOIN LATERAL jsonb_to_recordset(t.rls) AS r ("column" text, unit_type text);
END
$$;

CREATE OR REPLACE FUNCTION console.import_preview(document jsonb)
RETURNS TABLE (object text, schema_name text, key text, action text)
LANGUAGE plpgsql AS $$
DECLARE
    mark bigint;
    changes jsonb;
BEGIN
    IF NOT rbac.can('import_config') THEN
        RAISE EXCEPTION 'The token lacks the import_config permission'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    SELECT coalesce(max(h.id), 0) INTO mark FROM console.config_history AS h;

    BEGIN
        PERFORM console.import_config(document);

        WITH changed AS (
            SELECT h.object, h.schema_name, h.key, h.action, h.old_row, h.new_row
            FROM console.config_history AS h
            WHERE h.id > mark
        ),

        plain AS (
            SELECT
                c.object,
                c.schema_name,
                c.key,
                CASE
                    WHEN
                        c.action = 'update'
                        AND (c.old_row - 'updated_at' - 'updated_by')
                        = (c.new_row - 'updated_at' - 'updated_by')
                        THEN 'unchanged'
                    ELSE c.action
                END AS action
            FROM changed AS c
            WHERE c.object <> 'rls'
        ),

        rules AS (
            SELECT
                c.object,
                c.schema_name,
                c.key,
                CASE
                    WHEN bool_or(c.action = 'delete') AND bool_or(c.action = 'insert') THEN
                        CASE
                            WHEN
                                max(c.old_row ->> 'unit_type') FILTER (WHERE c.action = 'delete')
                                IS NOT DISTINCT FROM
                                max(c.new_row ->> 'unit_type') FILTER (WHERE c.action = 'insert')
                                THEN 'unchanged'
                            ELSE 'update'
                        END
                    WHEN bool_or(c.action = 'insert') THEN 'insert'
                    ELSE 'delete'
                END AS action
            FROM changed AS c
            WHERE c.object = 'rls'
            GROUP BY c.object, c.schema_name, c.key
        )

        SELECT coalesce(jsonb_agg(to_jsonb(r)), '[]'::jsonb) INTO changes
        FROM (
            SELECT * FROM plain
            UNION ALL
            SELECT * FROM rules
        ) AS r;

        RAISE EXCEPTION 'The preview rolls the import back' USING ERRCODE = 'PV001';
    EXCEPTION
        WHEN SQLSTATE 'PV001' THEN
            NULL;
    END;

    RETURN QUERY
    SELECT p.object, p.schema_name, p.key, p.action
    FROM jsonb_to_recordset(changes) AS p (object text, schema_name text, key text, action text)
    ORDER BY p.schema_name, p.object, p.key;
END
$$;

REVOKE ALL ON FUNCTION console.import_preview(jsonb) FROM PUBLIC;

REVOKE ALL ON FUNCTION console.sync_config() FROM PUBLIC;
REVOKE ALL ON FUNCTION console.import_config(jsonb) FROM PUBLIC;

CREATE OR REPLACE FUNCTION console.config() RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
    WITH members AS (
        SELECT
            schema,
            jsonb_agg(
                jsonb_strip_nulls(jsonb_build_object('name', name, 'cache_ttl', cache_ttl))
                ORDER BY name
            ) AS tables
        FROM console.tables
        GROUP BY schema
    )

    SELECT jsonb_build_object(
        'revision', (SELECT value FROM rbac.revision),
        'schemas',
        coalesce(
            jsonb_object_agg(
                s.name, jsonb_build_object('tables', coalesce(m.tables, '[]'::jsonb))
            ),
            '{}'::jsonb
        )
    )
    FROM console.schemas AS s
    LEFT JOIN members AS m ON s.name = m.schema
$$;

REVOKE ALL ON FUNCTION console.config() FROM PUBLIC;
