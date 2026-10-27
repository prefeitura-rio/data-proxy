{#
{
  "kind": "template",
  "description": "Create the table where the console keeps the sealed keys of the storage identities that it makes for each schema, in the catalog database. The secret of a key is sealed by the console, so the database never holds it in the clear. Only the signer login reads and writes the table, and it can't delete a row. The drop of a schema reader deletes the row of that schema. Run it in the catalog database as the owner role. Run it again without an error.",
  "inputs": {
    "signer": "SQL literal with the signer login, which must exist."
  }
}
#}
-- noqa: disable=CP02,PRS
CREATE SCHEMA IF NOT EXISTS signer;

CREATE TABLE IF NOT EXISTS signer.keys (
    schema_name text PRIMARY KEY,
    access_key text NOT NULL,
    secret_sealed text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

REVOKE ALL ON SCHEMA signer FROM PUBLIC;
REVOKE ALL ON signer.keys FROM PUBLIC;

DO $grant_signer$
BEGIN
    EXECUTE format('GRANT USAGE ON SCHEMA signer TO %I', {{ signer }});
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON signer.keys TO %I', {{ signer }});
END
$grant_signer$
