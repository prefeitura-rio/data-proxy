-- +goose Up
-- Migration 00011 replaced console.require_claim() and left out the two
-- settings that 00005 had put on it. A function that is replaced keeps no
-- setting that its new text does not say.
ALTER FUNCTION console.require_claim()
SECURITY DEFINER SET search_path = pg_catalog, pg_temp;

-- +goose Down
ALTER FUNCTION console.require_claim() SECURITY INVOKER RESET search_path;
