# Backups

A sync rebuilds application tables from BigQuery. It does not rebuild access grants. The chart backs up each `<schema>.access_policy` table.

## Operation

Set `backup.enabled` to create one CronJob per configured schema. Each job exports only `<schema>.access_policy` and uploads the dump to the separate backup S3/GCS-compatible service:

```text
<backup.prefix>/<schema>/<date>.dump
```

The chart default prefix is:

```text
backups/access_policy
```

Backups contain only access-policy data. They do not contain PostgreSQL tables, Parquet files, or the full database.

## Configuration

```yaml
backup:
  enabled: true
  schedule: "0 3 * * *"
  prefix: backups/access_policy
  existingSecret: data-proxy-backup
  s3:
    endpointURL: https://storage.googleapis.com
    bucket: access-policy-backups
```

The backup Secret supplies the PostgreSQL backup-role password and the separate object-store credentials. Configure bucket lifecycle rules for retention.

The default schedule is daily at 03:00 UTC. Configure bucket lifecycle rules for retention; the chart does not delete backup objects.

## Verify a backup

Download the dump and inspect it before restoring:

```bash
aws s3 cp \
  "s3://<bucket>/backups/access_policy/<schema>/<date>.dump" \
  access_policy.dump

pg_restore --list access_policy.dump
```

## Restore

1. Download the reviewed dump from the separate backup service.
2. Restore it into a temporary database or table.
3. Compare it with `<schema>.access_policy`.
4. Apply reviewed rows only.

Do not load an unreviewed backup into a live policy table.

---

[← Previous](metrics.md) · [Home](../README.md) · [Next →](development.md)
