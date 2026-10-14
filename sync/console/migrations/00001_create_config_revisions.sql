-- +goose Up
CREATE TABLE data_proxy.config_revisions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    author text NOT NULL,
    comment text NOT NULL DEFAULT '',
    document jsonb NOT NULL,
    active boolean NOT NULL DEFAULT false
);

CREATE UNIQUE INDEX config_revisions_one_active
ON data_proxy.config_revisions (active)
WHERE active;

-- +goose Down
DROP TABLE data_proxy.config_revisions;
