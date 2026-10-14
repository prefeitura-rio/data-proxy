-- +goose Up
CREATE SCHEMA IF NOT EXISTS data_proxy;

CREATE TABLE data_proxy.revisions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    author text NOT NULL,
    comment text NOT NULL DEFAULT '',
    document jsonb NOT NULL CHECK (
        jsonb_typeof(document -> 'schemas') IS NOT DISTINCT FROM 'object'
    ),
    valid boolean,
    errors jsonb,
    validated_at timestamptz,
    active boolean NOT NULL DEFAULT FALSE CHECK (NOT active OR valid),
    activated_at timestamptz
);

CREATE UNIQUE INDEX revisions_one_active
ON data_proxy.revisions (active)
WHERE active;

-- +goose Down
DROP TABLE data_proxy.revisions;
