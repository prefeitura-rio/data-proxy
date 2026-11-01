{#
{
  "kind": "template",
  "description": "Drop the role that reads the metadata of one schema, with its grants and default privileges in the catalog database.",
  "inputs": {
    "name": "SQL literal with the name of the schema."
  }
}
#}
{#
Run it after the metadata schema is gone.
Run it again without an error.
#}
DO $drop_reader$
DECLARE
    reader text := 'schema_' || {{ name }};
BEGIN
    IF to_regclass('signer.keys') IS NOT NULL THEN
        DELETE FROM signer.keys WHERE schema_name = {{ name }};
    END IF;

    IF EXISTS (SELECT FROM pg_roles WHERE rolname = reader) THEN
        EXECUTE format('DROP OWNED BY %I', reader);
        EXECUTE format('DROP ROLE %I', reader);
    END IF;
END
$drop_reader$
