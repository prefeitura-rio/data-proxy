# Query tab scope

The query tab issues signed links to Parquet files. Three layers scope the links to one schema:

| Layer | What it checks | What it stops |
| --- | --- | --- |
| Application database | `read_files`, the schema in the `schemas` claim, that the table exists, and that the schema isn't encrypted. It verifies the token. | A user who asks for a schema outside the claim. |
| Catalog role | The role `schema_<name>` reads the metadata of its schema only. | The console server that lists the files of another schema by mistake. |
| Signed links | A link works for one object and one signature. A credential limited to the prefix of a schema can't sign a key outside it. | A path that isn't in the list, a changed key, and a link for another file. |

The layers protect against a partial bug, such as a wrong table name or a path that a user supplies. They don't protect against a stolen console server. The server stores the catalog login and the signing credentials, and neither the catalog nor the storage sees the token of the user. Only the application database verifies the token. The server must therefore call `table_files` first, and then use the schema that it authorizes for the other two layers. A row policy keyed on a session setting isn't a layer, because the session can change the setting.

Row-level security doesn't apply to the files, so a user with `read_files` reads every row of the tables of their schemas. Only `dp_admin` has `read_files`.

With `console.storage.perSchemaIdentities`, the console makes one storage identity for each schema, with a policy that allows `s3:GetObject` on the folder of that schema only. Nothing lists the schemas: the first query of a schema makes its identity. The price is that the console stores an admin credential of the storage, so a stolen console can change the identities. When a schema leaves the console, the sync worker deletes its identity. A storage that can't limit a credential to a prefix, such as GCS, signs with one read-only credential. Then the first two layers are the only scope.

Every link is a `GET`. A `HEAD` request on a link signed for `GET` answers 403, and so does a changed signature. The notebook origin is a different site from the console, so the notebook can't read the cookie of the console.

<!-- nav:start -->
---

[Previous: Query tab](query.md) | [Next: Local Development](../development/index.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
