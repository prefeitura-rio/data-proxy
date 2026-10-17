-- +goose Up
CREATE VIEW console.health AS
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
    coalesce(o.view_count, 0) AS view_count
FROM console.schemas AS s
LEFT JOIN objects AS o ON s.name = o.name;

CREATE VIEW console.synced AS
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
FROM console.tables AS t
LEFT JOIN data_proxy.state AS st ON t.name = st.table_name;

-- +goose Down
DROP VIEW console.synced;
DROP VIEW console.health;
