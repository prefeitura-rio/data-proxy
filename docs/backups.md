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

## Schedule

The default schedule is `0 3 * * *` (daily at 03:00 UTC). Set `backup.schedule`
to change the interval. Set `backup.startingDeadlineSeconds` to control how
long Kubernetes waits for a missed schedule before it skips the job.

## Retention

The chart does not delete old backup objects. Objects accumulate in the configured
storage prefix. Set a lifecycle policy on the storage bucket to delete old backups
automatically. For example, keep 30 days of backups and delete older objects.

## Enabling backups

```yaml
backup:
  enabled: true
  ageRecipient: "age1..."
  password: "..."
  schedule: "0 3 * * *"
```

## Backup role

The init-db Job creates the `backup` role automatically. The Job also grants
`USAGE` on each application schema and `SELECT` on each `access_policy` table.
No manual SQL is needed.

## Verifying a backup

Download and decrypt a backup to verify it:

```bash
age --decrypt --identity key.txt \
  "s3://bucket/backup/pic/2026-09-09.csv.age" \
  --output verified.csv
```

Check that the CSV has the expected columns and row count:

```bash
head -1 verified.csv
wc -l verified.csv
```

## Restoring a backup

Restore is a manual operation.

1. Download and decrypt the schema backup.
2. Load it into a temporary table.
3. Compare it with `<schema>.access_policy`.
4. Apply only reviewed rows to the live local policy table.

Do not load an unreviewed backup directly into a live policy table.
