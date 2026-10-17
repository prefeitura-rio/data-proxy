-- +goose Up
CREATE OR REPLACE VIEW console.synced AS
SELECT
    t.schema,
    t.name,
    st.table_name IS NOT NULL AS synced,
    st.state ->> 'signature' AS signature,
    CASE
        WHEN jsonb_typeof(st.state -> 'partitions') = 'object'
            THEN (SELECT count(*) FROM jsonb_object_keys(st.state -> 'partitions'))::int
        ELSE 0
    END AS partition_count
FROM console.tables AS t
LEFT JOIN data_proxy.state AS st ON t.name = st.table_name
WHERE rbac.can('read_sync') AND rbac.mine(t.schema);

-- +goose Down
CREATE OR REPLACE VIEW console.synced AS
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
LEFT JOIN data_proxy.state AS st ON t.name = st.table_name
WHERE rbac.mine(t.schema);
