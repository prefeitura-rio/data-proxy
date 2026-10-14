-- +goose Up
CREATE TABLE IF NOT EXISTS data_proxy.state (
    table_name text PRIMARY KEY,
    state jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS data_proxy.errors (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reason text NOT NULL,
    fields jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);

-- +goose Down
DROP TABLE data_proxy.errors;
DROP TABLE data_proxy.state;
