-- +goose Up
CREATE TABLE data_proxy.schemas (
    name text PRIMARY KEY CHECK (name ~ '^[a-z][a-z0-9_]{0,62}$'),
    status text NOT NULL
    CHECK (status IN ('provisioning', 'ready', 'failed', 'detached')),
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- +goose Down
DROP TABLE data_proxy.schemas;
