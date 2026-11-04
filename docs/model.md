# Data model

This page shows the tables of the system. Each diagram has the columns that the code reads or writes. A line joins a column to the column that it references. The crow's foot marks the table that holds the reference.

A solid line is a foreign key. A dashed line joins two columns with the same value and no foreign key. The diagrams show no views and no functions. See [Database Schema](database.md) for those.

## All tables

This diagram joins the three diagrams below. It also shows the two links between them: `data_proxy.scope` to `dbos.workflow_status`, and `signer.keys` to the metadata schemas.

```mermaid
%% All database tables
erDiagram
  console_schemas["console.schemas"] {
    text name PK
    text source
    jsonb settings
    text claim
    boolean encrypted
    timestamptz updated_at
    text updated_by
  }
  console_tables["console.tables"] {
    text name PK
    text schema FK
    text strategy
    integer n
    boolean fallback
    integer cache_ttl
    jsonb partitioning
    text[] sort
    timestamptz updated_at
    text updated_by
  }
  console_rls["console.rls"] {
    bigint id PK
    text table_name FK
    text column_name
    text unit_type
    timestamptz updated_at
    text updated_by
  }
  console_config_history["console.config_history"] {
    bigint id PK
    timestamptz changed_at
    text changed_by
    text action
    text object
    text schema_name
    text key
    jsonb old_row
    jsonb new_row
  }
  data_proxy_state["data_proxy.state"] {
    text table_name PK
    jsonb state
  }
  data_proxy_errors["data_proxy.errors"] {
    bigint id PK
    text reason
    jsonb fields
    timestamptz recorded_at
  }
  data_proxy_scope["data_proxy.scope"] {
    text workflow_id PK
    text run_id
    text schema_name PK
    text table_name PK
  }
  rbac_roles["rbac.roles"] {
    text name PK
    text[] permissions
    text wrapped
  }
  rbac_revision["rbac.revision"] {
    boolean singleton PK
    bigint value
  }
  source_schema_access_policy["source_schema.access_policy"] {
    text subject UK
    text unit_type UK
    text unit_id UK
  }
  source_schema_access_log["source_schema.access_log"] {
    text subject
    text unit_type
    text unit_id
    text action
    timestamptz changed_at
  }
  dbos_workflow_status["dbos.workflow_status"] {
    text workflow_uuid PK
    text parent_workflow_id
    text name
    text status
    text queue_name
    text queue_partition_key
    text schedule_name
    text authenticated_user
    text executor_id
    text application_version
    bigint recovery_attempts
    bigint created_at
    bigint started_at_epoch_ms
    bigint completed_at
  }
  dbos_operation_outputs["dbos.operation_outputs"] {
    text workflow_uuid PK, FK
    integer function_id PK
    text function_name
    text child_workflow_id
    text error
    bigint started_at_epoch_ms
    bigint completed_at_epoch_ms
  }
  dbos_streams["dbos.streams"] {
    text workflow_uuid PK, FK
    text key PK
    integer offset PK
    text value
    text serialization
  }
  dbos_queues["dbos.queues"] {
    text queue_id PK
    text name
    integer concurrency
    integer worker_concurrency
    integer rate_limit_max
    double_precision rate_limit_period_sec
    boolean partition_queue
    integer partition_concurrency
    double_precision polling_interval_sec
    bigint updated_at
  }
  dbos_workflow_schedules["dbos.workflow_schedules"] {
    text schedule_name PK
    text workflow_name
    text schedule
    text status
    text queue_name
    text cron_timezone
    boolean automatic_backfill
    text last_fired_at
  }
  signer_keys["signer.keys"] {
    text schema_name PK
    text access_key
    text secret_sealed
    timestamptz created_at
  }
  metadata_ducklake_metadata["ducklake_metadata"] {
    text key
    text value
    text scope
  }
  metadata_ducklake_schema["ducklake_schema"] {
    bigint schema_id
    text schema_name
    text path
    boolean path_is_relative
    bigint end_snapshot
  }
  metadata_ducklake_table["ducklake_table"] {
    bigint table_id
    bigint schema_id
    text table_name
    text path
    boolean path_is_relative
    bigint end_snapshot
  }
  metadata_ducklake_data_file["ducklake_data_file"] {
    bigint data_file_id
    bigint table_id
    text path
    boolean path_is_relative
    bigint file_order
    bigint end_snapshot
  }
  metadata_ducklake_delete_file["ducklake_delete_file"] {
    bigint data_file_id
    text path
    boolean path_is_relative
    bigint end_snapshot
  }
  metadata_ducklake_sort_info["ducklake_sort_info"] {
    bigint sort_id
    bigint table_id
    bigint end_snapshot
  }
  metadata_ducklake_sort_expression["ducklake_sort_expression"] {
    bigint sort_id
    bigint sort_key_index
    text expression
  }
  console_tables }o--|| console_schemas : "schema"
  console_rls }o--|| console_tables : "table_name"
  data_proxy_scope }o..|| console_schemas : "same name, no foreign key"
  console_config_history }o..|| console_schemas : "same name, no foreign key"
  source_schema_access_policy ||..o{ source_schema_access_log : "trigger writes"
  dbos_workflow_status }o..o| dbos_workflow_status : "parent run"
  dbos_operation_outputs }o--|| dbos_workflow_status : "workflow_uuid"
  dbos_streams }o--|| dbos_workflow_status : "workflow_uuid"
  dbos_workflow_status }o..o| dbos_queues : "same name"
  dbos_workflow_status }o..o| dbos_workflow_schedules : "schedule_name"
  metadata_ducklake_table }o--|| metadata_ducklake_schema : "schema_id"
  metadata_ducklake_data_file }o--|| metadata_ducklake_table : "table_id"
  metadata_ducklake_delete_file }o--|| metadata_ducklake_data_file : "data_file_id"
  metadata_ducklake_sort_info }o--|| metadata_ducklake_table : "table_id"
  metadata_ducklake_sort_expression }o--|| metadata_ducklake_sort_info : "sort_id"
  data_proxy_scope }o..|| dbos_workflow_status : "same value, no foreign key"
  signer_keys }o..o| metadata_ducklake_metadata : "same name as the metadata schema"
```

## Application database

The application database `data-proxy` has four groups of tables.

```mermaid
%% Application database tables
erDiagram
  console_schemas["console.schemas"] {
    text name PK
    text source
    jsonb settings
    text claim
    boolean encrypted
    timestamptz updated_at
    text updated_by
  }
  console_tables["console.tables"] {
    text name PK
    text schema FK
    text strategy
    integer n
    boolean fallback
    integer cache_ttl
    jsonb partitioning
    text[] sort
    timestamptz updated_at
    text updated_by
  }
  console_rls["console.rls"] {
    bigint id PK
    text table_name FK
    text column_name
    text unit_type
    timestamptz updated_at
    text updated_by
  }
  console_config_history["console.config_history"] {
    bigint id PK
    timestamptz changed_at
    text changed_by
    text action
    text object
    text schema_name
    text key
    jsonb old_row
    jsonb new_row
  }
  data_proxy_state["data_proxy.state"] {
    text table_name PK
    jsonb state
  }
  data_proxy_errors["data_proxy.errors"] {
    bigint id PK
    text reason
    jsonb fields
    timestamptz recorded_at
  }
  data_proxy_scope["data_proxy.scope"] {
    text workflow_id PK
    text run_id
    text schema_name PK
    text table_name PK
  }
  rbac_roles["rbac.roles"] {
    text name PK
    text[] permissions
    text wrapped
  }
  rbac_revision["rbac.revision"] {
    boolean singleton PK
    bigint value
  }
  source_schema_access_policy["source_schema.access_policy"] {
    text subject UK
    text unit_type UK
    text unit_id UK
  }
  source_schema_access_log["source_schema.access_log"] {
    text subject
    text unit_type
    text unit_id
    text action
    timestamptz changed_at
  }
  console_tables }o--|| console_schemas : "schema"
  console_rls }o--|| console_tables : "table_name"
  data_proxy_scope }o..|| console_schemas : "same name, no foreign key"
  console_config_history }o..|| console_schemas : "same name, no foreign key"
  source_schema_access_policy ||..o{ source_schema_access_log : "trigger writes"
```

- **Configuration (`console`):** `schemas`, `tables` and `rls` store the sync configuration. A table belongs to one schema, and a row policy rule (`rls`) belongs to one table. The name of a schema can't be a reserved name. `config_history` records every change of these three tables. See [Configuration](console/configuration.md) and [Changes](console/changes.md).
- **Engine (`data_proxy`):** `state` has the state of each synced table as JSON. `errors` has the engine errors. `scope` links a workflow to the schemas and tables that a sync run covers. The console views use it to show a user only the sync runs of its own schemas.
- **Rules (`rbac`):** `roles` has the permissions of each role, filled from `rbac.toml`. `revision` is a single row with a counter. The proxy reads the counter to find a change of the rules.
- **Access policy (one schema for each source schema):** `access_policy` has the subjects that may read a unit, and `access_log` records each change. A trigger writes the log. See [Authorization](security/authorization.md).

The `data_proxy` and `console` tables don't reference each other with foreign keys. `scope.schema_name` and `config_history.schema_name` carry the name of a schema, and nothing enforces it.

## Catalog database

The `ducklake` database has two kinds of schema.

```mermaid
%% Catalog database tables
erDiagram
  signer_keys["signer.keys"] {
    text schema_name PK
    text access_key
    text secret_sealed
    timestamptz created_at
  }
  metadata_ducklake_metadata["ducklake_metadata"] {
    text key
    text value
    text scope
  }
  metadata_ducklake_schema["ducklake_schema"] {
    bigint schema_id
    text schema_name
    text path
    boolean path_is_relative
    bigint end_snapshot
  }
  metadata_ducklake_table["ducklake_table"] {
    bigint table_id
    bigint schema_id
    text table_name
    text path
    boolean path_is_relative
    bigint end_snapshot
  }
  metadata_ducklake_data_file["ducklake_data_file"] {
    bigint data_file_id
    bigint table_id
    text path
    boolean path_is_relative
    bigint file_order
    bigint end_snapshot
  }
  metadata_ducklake_delete_file["ducklake_delete_file"] {
    bigint data_file_id
    text path
    boolean path_is_relative
    bigint end_snapshot
  }
  metadata_ducklake_sort_info["ducklake_sort_info"] {
    bigint sort_id
    bigint table_id
    bigint end_snapshot
  }
  metadata_ducklake_sort_expression["ducklake_sort_expression"] {
    bigint sort_id
    bigint sort_key_index
    text expression
  }
  metadata_ducklake_table }o--|| metadata_ducklake_schema : "schema_id"
  metadata_ducklake_data_file }o--|| metadata_ducklake_table : "table_id"
  metadata_ducklake_delete_file }o--|| metadata_ducklake_data_file : "data_file_id"
  metadata_ducklake_sort_info }o--|| metadata_ducklake_table : "table_id"
  metadata_ducklake_sort_expression }o--|| metadata_ducklake_sort_info : "sort_id"
```

- **`signer.keys`:** one row for each schema. It has the storage access key of the schema and the sealed secret. The console reads it to sign file links. See [Query tab](console/query.md).
- **Metadata schemas:** DuckLake creates one schema for each source schema, with the name of the source schema. It has many tables. The diagram shows only the tables that Data Proxy reads. The console reads `ducklake_table`, `ducklake_data_file` and `ducklake_delete_file` to list the files of a table. The sync service reads the sort tables to find the sort keys.

An empty `end_snapshot` marks a row that's still current. The code filters on it.

## Workflow tables

DBOS owns the `dbos` schema in the application database. The diagram shows the tables that the console views read.

```mermaid
%% DBOS workflow tables
erDiagram
  dbos_workflow_status["dbos.workflow_status"] {
    text workflow_uuid PK
    text parent_workflow_id
    text name
    text status
    text queue_name
    text queue_partition_key
    text schedule_name
    text authenticated_user
    text executor_id
    text application_version
    bigint recovery_attempts
    bigint created_at
    bigint started_at_epoch_ms
    bigint completed_at
  }
  dbos_operation_outputs["dbos.operation_outputs"] {
    text workflow_uuid PK, FK
    integer function_id PK
    text function_name
    text child_workflow_id
    text error
    bigint started_at_epoch_ms
    bigint completed_at_epoch_ms
  }
  dbos_streams["dbos.streams"] {
    text workflow_uuid PK, FK
    text key PK
    integer offset PK
    text value
    text serialization
  }
  dbos_queues["dbos.queues"] {
    text queue_id PK
    text name
    integer concurrency
    integer worker_concurrency
    integer rate_limit_max
    double_precision rate_limit_period_sec
    boolean partition_queue
    integer partition_concurrency
    double_precision polling_interval_sec
    bigint updated_at
  }
  dbos_workflow_schedules["dbos.workflow_schedules"] {
    text schedule_name PK
    text workflow_name
    text schedule
    text status
    text queue_name
    text cron_timezone
    boolean automatic_backfill
    text last_fired_at
  }
  dbos_workflow_status }o..o| dbos_workflow_status : "parent run"
  dbos_operation_outputs }o--|| dbos_workflow_status : "workflow_uuid"
  dbos_streams }o--|| dbos_workflow_status : "workflow_uuid"
  dbos_workflow_status }o..o| dbos_queues : "same name"
  dbos_workflow_status }o..o| dbos_workflow_schedules : "schedule_name"
```

- **`workflow_status`:** one row for each workflow. A sync run is a row with the name `run_sync`. Its tasks are rows with `parent_workflow_id` set to the id of the run.
- **`operation_outputs` and `streams`:** the steps and the log lines of a workflow. Both reference `workflow_status` with a foreign key.
- **`queues` and `workflow_schedules`:** the queues and the schedules. A workflow names its queue and its schedule.

The console views only read these tables. The console starts and controls a workflow with `dbos.enqueue_workflow()` and the `control` queue. See [Control a run and a schedule](console/runs.md). The column `data_proxy.scope.workflow_id` has the same value as `workflow_uuid`, so a view can filter the workflows by schema.

## Change a diagram

Edit the table in its own diagram on this page, and in the `All tables` diagram. Mermaid has no import, so the two copies need the same change. Take the columns from the SQL files in `console/migrations/` and `helm/files/templates/postgres/`. For the DBOS tables, take them from the `dbos` migrations in the Python package.

<!-- nav:start -->
---

[Previous: Database roles and grants](grants.md) | [Next: Glossary](glossary.md) | [Home](index.md)
<!-- nav:end -->
