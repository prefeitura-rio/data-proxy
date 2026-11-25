-- +goose Up
-- Installs ltree and adds console.catalog: one row for each schema and one for each
-- table, with its project, its short schema name, its table name, its physical schema
-- and its ltree path (project.schema or project.schema.table). The view runs with
-- the rights of the caller, so the row policies of the tables decide what a token
-- sees. A name that ltree can't hold gets no path, and the view still reads.
CREATE EXTENSION IF NOT EXISTS ltree;

CREATE VIEW console.catalog WITH (security_invoker = TRUE) AS
WITH entries AS (
    SELECT
        s.project,
        s.short_name AS schema_name,
        NULL::text AS table_name,
        s.name AS physical_schema
    FROM console.schemas AS s
    UNION ALL
    SELECT
        s.project,
        s.short_name,
        regexp_replace(t.name, '^.*\.', '') AS table_name,
        s.name AS physical_schema
    FROM console.tables AS t
    INNER JOIN console.schemas AS s ON t.schema = s.name
)

SELECT
    project,
    schema_name,
    table_name,
    physical_schema,
    CASE
        WHEN concat_ws('.', project, schema_name, table_name)
            ~ '^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+){1,2}$'
            THEN text2ltree(concat_ws('.', project, schema_name, table_name))
    END AS path
FROM entries;

-- +goose Down
DROP VIEW console.catalog;
DROP EXTENSION ltree;
