-- +goose Up
-- noqa: disable=CP02,PRS
-- The check of a row rule and the check of a cleared claim each read what
-- the other has not committed, so two editors could both pass. The share lock
-- on the schema row makes a clear of the claim wait for the rule insert.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION console.require_claim() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM console.tables AS t
        INNER JOIN console.schemas AS s ON t.schema = s.name
        WHERE t.name = new.table_name AND s.claim IS NOT NULL
        FOR SHARE OF s
    ) THEN
        RAISE EXCEPTION 'The schema of table % has no claim, so it can''t have rls rules',
            new.table_name USING ERRCODE = 'check_violation';
    END IF;
    RETURN new;
END
$$;
-- +goose StatementEnd

-- +goose Down
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION console.require_claim() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM console.tables AS t
        INNER JOIN console.schemas AS s ON t.schema = s.name
        WHERE t.name = new.table_name AND s.claim IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'The schema of table % has no claim, so it can''t have rls rules',
            new.table_name USING ERRCODE = 'check_violation';
    END IF;
    RETURN new;
END
$$;
-- +goose StatementEnd
