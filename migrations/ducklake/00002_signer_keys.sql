-- +goose Up
-- noqa: disable=CP02,RF05
-- Creates signer.keys, which stores the sealed storage key of each schema. Only the
-- signer login (AUTH_SIGNER_ROLE) reads and writes it, and it can't delete a row. The
-- login must exist before the file runs.
CREATE SCHEMA IF NOT EXISTS signer;

CREATE TABLE IF NOT EXISTS signer.keys (
    schema_name text PRIMARY KEY,
    access_key text NOT NULL,
    secret_sealed text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

REVOKE ALL ON SCHEMA signer FROM PUBLIC;

REVOKE ALL ON signer.keys FROM PUBLIC;


-- Goose fills ${...} only between ENVSUB ON and ENVSUB OFF, and it turns a dollar sign
-- there into a plain one, so keep a $$ body outside. Each name is quoted, so a name
-- that is not set stops the run.
-- +goose ENVSUB ON
GRANT USAGE ON SCHEMA signer TO "${AUTH_SIGNER_ROLE}";

GRANT SELECT, INSERT, UPDATE ON signer.keys TO "${AUTH_SIGNER_ROLE}";
-- +goose ENVSUB OFF

-- +goose Down
DROP TABLE signer.keys;

DROP SCHEMA signer;
