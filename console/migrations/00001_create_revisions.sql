-- +goose Up
CREATE SCHEMA IF NOT EXISTS data_proxy;

CREATE TABLE data_proxy.revisions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    author text NOT NULL,
    comment text NOT NULL DEFAULT '',
    document jsonb NOT NULL,
    active boolean NOT NULL DEFAULT false
);

CREATE UNIQUE INDEX revisions_one_active
ON data_proxy.revisions (active)
WHERE active;

-- +goose Down
DROP TABLE data_proxy.revisions;
