{#
{
  "kind": "template",
  "description": "Give the metadata schema, the reader role and the signer keys of a schema their new name in the catalog database, when they still have the old one.",
  "inputs": {
    "old": "SQL literal with the old name of the schema.",
    "new": "SQL literal with the new name of the schema."
  }
}
#}
{#
Run it in the catalog database as the owner role.
It renames only what has the old name and has no new name, so it changes nothing when the new name exists.
The grants, the default privileges and the membership of the signer follow the rename.
Run it again without an error.
#}
DO $rename_reader$
BEGIN
    IF {{ old }} <> {{ new }} THEN
        IF to_regnamespace(quote_ident({{ old }})) IS NOT NULL
            AND to_regnamespace(quote_ident({{ new }})) IS NULL THEN
            EXECUTE format('ALTER SCHEMA %I RENAME TO %I', {{ old }}, {{ new }});
        END IF;

        IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'schema_' || {{ old }})
            AND NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'schema_' || {{ new }}) THEN
            EXECUTE format('ALTER ROLE %I RENAME TO %I', 'schema_' || {{ old }}, 'schema_' || {{ new }});
        END IF;

        IF to_regclass('signer.keys') IS NOT NULL THEN
            UPDATE signer.keys SET schema_name = {{ new }} WHERE schema_name = {{ old }};
        END IF;
    END IF;
END
$rename_reader$
