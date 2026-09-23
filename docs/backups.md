# Backups

A sync rebuilds application tables from BigQuery. It doesn't rebuild access grants. The chart backs up each `<schema>.access_policy` table and, when configured, its `<schema>.access_log` audit trail.

## Operation

Set `backup.enabled` to create one CronJob per configured schema. Each job exports `<schema>.access_policy` and `<schema>.access_log`, uploads both dumps to the separate backup S3/GCS-compatible service, then prunes `access_log` rows older than `backup.accessLog.retentionDays`:

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

The shared `jobs` Secret supplies the PostgreSQL maintenance-role password. The backup CronJob and the cleanup CronJob both connect as the `jobs` role, so neither runs as the database owner.

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

---

[← Previous](metrics.md) · [Home](../README.md) · [Next →](development.md)
