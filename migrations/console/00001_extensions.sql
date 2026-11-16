-- +goose NO TRANSACTION
-- +goose Up
-- Installs the postgis and pg_duckdb extensions. The file runs outside a transaction,
-- because goose can't write its version row after CREATE EXTENSION pg_duckdb inside
-- one.
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE EXTENSION IF NOT EXISTS pg_duckdb;
