# Changes

These sections describe how the console records changes to the configuration, and how it previews an import.

## History of the configuration

`console.config_history` records every insert, update and delete in `schemas`, `tables` and `rls`. A row contains the time, the user name, the schema, the key, and the old and the new row. The trigger `record_change` writes it, and nobody else can.

A token with `read_config` reads the rows of the schemas in its claim, and the Config tab shows them.

The history doesn't record a rule that the database deletes together with its table. The history row of the table delete already shows the change.

## Import preview

`console.import_preview(document)` runs `import_config` in a sub-transaction, reads the rows that the history trigger wrote, and rolls the import back.

It returns one row for each schema, table and rule with the action `insert`, `update`, `delete` or `unchanged`. A token needs `import_config`.

The Config tab shows the preview before it imports. The import applies only after you choose Apply import.

<!-- nav:start -->
---

[Previous: Configuration](configuration.md) | [Next: Query tab](query.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
