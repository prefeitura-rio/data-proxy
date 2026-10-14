-- +goose Up
CREATE VIEW admin.health AS
WITH objects AS (
    SELECT
        n.nspname AS name,
        bool_or(c.relname = 'access_policy') AS access_policy,
        count(c.oid) FILTER (WHERE c.relkind = 'v')::int AS view_count
    FROM pg_catalog.pg_namespace AS n
    LEFT JOIN pg_catalog.pg_class AS c ON n.oid = c.relnamespace
    GROUP BY n.nspname
)

SELECT
    s.name,
    o.name IS NOT NULL AS schema_exists,
    coalesce(o.access_policy, FALSE) AS access_policy,
    r.rolname IS NOT NULL AS policy_writer,
    coalesce(o.view_count, 0) AS view_count
FROM admin.schemas AS s
LEFT JOIN objects AS o ON s.name = o.name
LEFT JOIN pg_catalog.pg_roles AS r ON 'policy_writer_' || s.name = r.rolname;

CREATE VIEW admin.synced AS
SELECT
    t.schema,
    t.name,
    st.table_name IS NOT NULL AS synced,
    st.state ->> 'signature' AS signature,
    coalesce(
        jsonb_array_length(
            jsonb_path_query_array(st.state, '$.partitions.keyvalue().key')
        ),
        0
    ) AS partition_count
FROM admin.tables AS t
LEFT JOIN data_proxy.state AS st ON t.name = st.table_name;

-- +goose Down
DROP VIEW admin.synced;
DROP VIEW admin.health;
