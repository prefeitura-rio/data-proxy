# Backups

A sync rebuilds application tables from BigQuery. It doesn't rebuild access grants. The chart backs up each `<schema>.access_policy` table and, when configured, its `<schema>.access_log` audit trail.

## Operation

Set `backup.enabled` to create the backup CronJob. It reads the schemas from the `console.schemas` table, so a schema that an editor adds in the console is backed up from its first night. For each schema it exports `<schema>.access_policy` and `<schema>.access_log`, uploads both dumps to the separate backup S3/GCS-compatible service, then prunes `access_log` rows older than `backup.accessLog.retentionDays`. A failure in one schema doesn't stop the others. The job fails at the end and names the schemas that failed. The objects are:

```text
<backup.prefix>/<schema>/<date>/access_policy.dump
<backup.prefix>/<schema>/<date>/access_log.dump
```

The chart default prefix is:

```text
backups/access_policy
```

Backups contain only access-policy and audit-log data. They don't contain PostgreSQL tables, Parquet files, or the full database.

## Configuration

```yaml
jobs:
  existingSecret: data-proxy-jobs
backup:
  enabled: true
  schedule: "0 3 * * *"
  prefix: backups/access_policy
  accessLog:
    retentionDays: 90
  s3:
    endpointURL: https://storage.googleapis.com
    bucket: access-policy-backups
```

The database Secret supplies the password of the `ops` role. The backup CronJob and the cleanup CronJob both connect as that role, like every other internal workload.

The default schedule is daily at 03:00 UTC. Configure bucket lifecycle rules for retention; the chart doesn't delete backup objects.

## Verify a backup

Download the dump and inspect it before restoring:

```bash
rclone copy \
  ":s3:<bucket>/backups/access_policy/<schema>/<date>/access_policy.dump" \
  access_policy.dump

pg_restore --list access_policy.dump
```

## Restore

1. Download the reviewed dump from the separate backup service.
2. Restore it into a temporary database or table.
3. Compare it with `<schema>.access_policy`.
4. Apply reviewed rows only.

Don't load an unreviewed backup into a live policy table.

## Catalog recovery

CNPG backs up the DuckLake catalogs with the rest of the cluster, because they live in the `ducklake` database. This chart doesn't back them up, and the backup CronJob dumps only `access_policy` and `access_log`. Restore the catalogs with the CNPG recovery of the whole cluster.

---

[← Previous](metrics.md) · [Home](../README.md) · [Next →](development.md)
