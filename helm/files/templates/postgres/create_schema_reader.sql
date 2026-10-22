{#
{
  "kind": "template",
  "description": "Create the role that reads the metadata of one schema in the catalog database, and nothing else. The role is named schema_ and the schema. It has no login. It gets USAGE on the metadata schema and SELECT on its tables, those that exist now and those that DuckLake creates later. The signer login is a member of the role and doesn't inherit its rights, so the signer must switch into the role to read a schema. The template creates the metadata schema when it is missing, because DuckLake creates its tables in a schema that exists. The template revokes the rights of PUBLIC on the metadata schema. A role name over 63 characters fails, because PostgreSQL would cut it and two schemas could share a role. Run it in the catalog database as the owner role. Run it again without an error.",
  "inputs": {
    "name": "SQL literal with the name of the schema.",
    "signer": "SQL literal with the signer login, which must exist.",
    "writer": "SQL literal with the role that creates the metadata tables."
  }
}
#}
-- noqa: disable=PRS
DO $create_reader$
DECLARE
    reader text := 'schema_' || {{ name }};
BEGIN
    IF length(reader) > 63 THEN
        RAISE EXCEPTION 'The role name % is too long', reader;
    END IF;

    EXECUTE format('CREATE SCHEMA IF NOT EXISTS %I', {{ name }});

    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = reader) THEN
        EXECUTE format('CREATE ROLE %I NOLOGIN', reader);
    END IF;

    EXECUTE format('REVOKE ALL ON SCHEMA %I FROM PUBLIC', {{ name }});
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', {{ name }}, reader);
    EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO %I', {{ name }}, reader);
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT SELECT ON TABLES TO %I',
        {{ writer }}, {{ name }}, reader
    );
    EXECUTE format('GRANT %I TO %I', reader, {{ signer }});
END
$create_reader$
