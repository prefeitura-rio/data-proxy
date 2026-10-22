# Query tab

The query tab lets a user query the synced data in a notebook. These sections describe the tab and the scope of its token.

## The query tab

A role with `read_files` queries the synced data in a notebook in the browser. The notebook is a marimo export that runs Python (Pyodide) and DuckDB compiled to WebAssembly. The files stay in object storage, and the browser reads them directly.

The notebook runs on its own origin, `notebooks.<domain>`, which is a different site from the console, and the console page embeds it in an `iframe`. The notebook has no token and no cookie of the console.

The grant that the console server builds for one schema has four steps:

1. **Authorize.** The server calls `GET /rpc/table_files?schema_name=<schema>&table_name=<source table>` with the token of the user. `console.table_files` needs `read_files` and checks the schemas of the token.
   - It answers 403 for a schema or a table outside the schemas, for a name that doesn't exist, and for a token without the permission.
   - It answers 409 for a schema with encrypted files, because the browser has no key.
   - The engine generates `dl_table_files` in every schema, and the function calls it.
2. **List.** The server connects to the catalog database with the `console_signer` login and runs `SET LOCAL ROLE schema_<name>` for the schema that step 1 authorized. That role reads the metadata of its schema only. The server reads the data files and the delete files of each table at the latest snapshot.
3. **Sign.** The server signs a `GET` link for each listed path, valid for 5 minutes. PostgREST can't sign links, so the database never contains storage credentials.
   - The credential is the identity of that schema. It's a user in the storage with a policy that allows `s3:GetObject` on the folder of the schema and nothing else.
   - The console makes the identity on the first query of the schema, through the IAM API of the storage, with an admin credential (`S3_ADMIN_ACCESS_KEY`, `S3_ADMIN_SECRET_KEY`).
   - The storage shows the secret of a key only once. The console therefore seals it and stores it in `signer.keys` in the catalog database.
   - An advisory lock keeps two replicas from making two keys. If the console can't open a stored key, it makes a new one.
   - Without the admin credential, every schema signs with one read-only credential.
4. **Send.** The page sends `{ schema, tables, expiresAt }` to the notebook with `postMessage` and the exact origin of the notebook. It sends a new grant every 4 minutes. The notebook builds one view for each table. A data file with a delete file becomes an anti join that removes the deleted rows, so the counts match the database.

These objects support the grant:

- `provision` creates the role `schema_<name>` with the schema and drops it with the schema. The role has no login. It has `USAGE` on the metadata schema of its schema and `SELECT` on its tables, now and for the tables that DuckLake creates later.
- `configure-db` creates the `console_signer` login without inheritance. Its password is in the Secret `<release>-signer`. The login is a member of every `schema_<name>` role and must switch into one.
- `configure-db` also creates the table `signer.keys` in the catalog database. Only that login reads and writes it. The drop of a schema deletes its row.
- The sync worker removes the identity in the storage. The step `cleanup_s3_credentials` runs in the workflows `provision_schemas` and `cleanup_tables`. It lists the users named `sig_<schema>`. For each user whose schema the configuration no longer lists, it deletes the keys, the policies and the user.
- The step runs only when `S3_IDENTITIES` is `true`, which the chart sets with `console.storage.perSchemaIdentities`. It uses the admin credential that the worker already has for DuckLake.
- A schema name has at most 56 characters, so that the role name fits in 63.

The files contain every row, and row-level security doesn't apply to them. That's the reason why only roles with `read_files` can list them. [Query tab scope](scope.md) describes what each layer stops and what the layers don't stop.

The bucket needs a cross-origin resource sharing (CORS) rule for the origin of the notebook, or the browser can't read the files. The rule allows `GET` and `HEAD`, allows the `Range` header, and exposes `Content-Length`, `Content-Range`, `ETag`, and `Accept-Ranges`. Without `Content-Range`, DuckDB reads a wrong size and fails with the message No magic bytes found. Set `s3.bootstrap.corsOrigins` to the notebook origins, and the bootstrap Job sets the rule through the S3 API of SeaweedFS or S3. The Job needs the permission to change the bucket, and it skips a GCS endpoint. GCS needs its own command.

The notebook origin sends a Content Security Policy that allows only its own files and the storage host. [Notebooks](../notebooks.md) describes the build of the site.

<!-- nav:start -->
---

[Previous: Changes](changes.md) | [Next: Query tab scope](scope.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
