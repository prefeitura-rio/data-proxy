{#
{
  "kind": "template",
  "description": "Let the catalog reader role read every metadata schema that the writer role creates, now and later.",
  "inputs": {
    "writer_role": "Role that creates the metadata schemas.",
    "reader_role": "Role that reads them."
  }
}
#}
ALTER DEFAULT PRIVILEGES FOR ROLE {{ writer_role }}
    GRANT USAGE ON SCHEMAS TO {{ reader_role }};
ALTER DEFAULT PRIVILEGES FOR ROLE {{ writer_role }}
    GRANT SELECT ON TABLES TO {{ reader_role }}
