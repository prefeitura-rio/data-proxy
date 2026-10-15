-- +goose Up
-- A table that isn't partitioned keeps "partitions": null in its state, and
-- keyvalue() fails on a null. Count the keys only when partitions is an object.
CREATE OR REPLACE VIEW admin.synced AS
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
FROM admin.tables AS t
LEFT JOIN data_proxy.state AS st ON t.name = st.table_name
WHERE admin.mine(t.schema);

-- +goose Down
CREATE OR REPLACE VIEW admin.synced AS
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
LEFT JOIN data_proxy.state AS st ON t.name = st.table_name
WHERE admin.mine(t.schema);
