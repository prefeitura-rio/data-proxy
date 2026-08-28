# Backups

## Scope

A sync rebuilds application tables from BigQuery. It does not rebuild access grants.
The chart backs up each `<schema>.access_policy` table separately.

## How it works

Set `backup.enabled` to create one CronJob per configured application schema.
Each job:

1. connects as the `backup` role to the schema writer;
2. exports `<schema>.access_policy` as CSV;
3. encrypts the export with [`age`](https://github.com/FiloSottile/age);
4. uploads it to `<backup.prefix>/<schema>/<date>.csv.age`.

In standalone mode, all jobs connect to the same PostgreSQL service. In HA mode,
each job connects to its schema HAProxy writer endpoint.

The chart does not store the `age` private key. Keep it outside the cluster.

## Enabling backups

```yaml
backup:
  enabled: true
  ageRecipient: "age1..."
  password: "..."
```

The backup role needs local schema access:

```sql
GRANT USAGE ON SCHEMA bcadastro TO backup;
GRANT SELECT ON bcadastro.access_policy TO backup;
```

## Restoring a backup

Restore is a manual operation.

1. Download and decrypt the schema backup.
2. Load it into a temporary table.
3. Compare it with `<schema>.access_policy`.
4. Apply only reviewed rows to the live local policy table.

Do not load an unreviewed backup directly into a live policy table.
