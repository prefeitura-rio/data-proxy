-- +goose Up
-- noqa: disable=CP02,PRS
-- A schema gets the database role schema_ and its name, at most 63 characters.
ALTER TABLE console.schemas DROP CONSTRAINT schemas_name_check;
ALTER TABLE console.schemas ADD CONSTRAINT schemas_name_check
CHECK (name ~ '^[a-z][a-z0-9_]{0,55}$');
