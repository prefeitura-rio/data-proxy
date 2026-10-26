# Backups

A sync rebuilds application tables from the source. It doesn't rebuild access grants. The chart backs up each `<schema>.access_policy` table and, when configured, its `<schema>.access_log` audit trail.

## Operation

Set `backup.enabled` to create the backup CronJob. It reads the schemas from the `console.schemas` table, so it backs up a schema that an editor adds in the console from its first night.

For each schema it exports `<schema>.access_policy` and `<schema>.access_log`, uploads both dumps to the separate backup S3/GCS-compatible service, then prunes `access_log` rows older than `backup.accessLog.retentionDays`. A failure in one schema doesn't stop the others. The job fails at the end and names the schemas that failed. The objects are:

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

The default schedule is daily at 03:00 UTC. Configure bucket lifecycle rules for retention. The chart doesn't delete backup objects.

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

Don't load an unreviewed backup into a live access policy table.

## Database backup

The whole database, with the DuckLake catalogs and the console data, has its own backup. Set `cnpg.backup.enabled` to turn it on. CloudNativePG (CNPG) then archives the write-ahead log (WAL) and takes a base backup each day, through the Barman Cloud plugin.

The plugin must run in the cluster before you turn the backup on, because the chart creates an `ObjectStore` that the plugin owns. The plugin has its own Helm chart, `plugin-barman-cloud`, in the namespace of the CNPG operator.

| Value | Contains |
| --- | --- |
| `cnpg.backup.bucket`, `cnpg.backup.destinationPath` | The bucket and the prefix of the backups. |
| `cnpg.backup.endpointURL` | The S3-compatible endpoint. For GCS, use `https://storage.googleapis.com`. |
| `cnpg.backup.existingSecret` | A Secret with `S3_ACCESS_KEY` and `S3_SECRET_KEY`. |
| `cnpg.backup.schedule` | The schedule of the base backups, with six fields. The first one is the seconds. |
| `cnpg.backup.retention` | The age after which the plugin deletes a backup and its WAL files. |

The chart takes the first base backup when it creates the schedule. `kubectl get backup` lists the backups, and `kubectl get cluster -o jsonpath='{.status.firstRecoverabilityPoint}'` shows the oldest point that you can restore.

The chart tells the AWS client to add checksums only when the storage requires them, because GCS refuses the default. A replica that falls behind catches up from the archive. The chart also limits what a replication slot keeps with `max_slot_wal_keep_size`, which is 8 GB by default. Without a limit, a slot that stops could fill the disk of the primary. Set a larger value in `cnpg.postgresql.parameters` for a larger disk.

## Turn the backup on in a running cluster

The plugin adds a sidecar to each instance pod, and CNPG restarts the primary in place, because a primary without the sidecar can't switch over. With two instances, the standby can't become ready while the primary is down. The cluster then stays in the phase `Primary instance is being restarted without a switchover`.

To end the wait, set `status.targetPrimary` of the Cluster to the standby, with the phase `Switchover in progress`. CNPG then starts the pods again. Recreate each instance pod once afterward, so that it reads a change in `instanceSidecarConfiguration`.

CNPG also refuses to change the image and a PostgreSQL parameter in one release when `primaryUpdateMethod` is `switchover`. Roll out a new parameter first with the image that runs now, and then roll out the new image.

## Restore the database

Restore the database with the recovery of the whole cluster. Create a new cluster with `bootstrap.recovery` that names the object store as its source. Check the new cluster before you point any workload at it, and keep the old cluster until the check ends.

<!-- nav:start -->
---

[Previous: Metrics](metrics.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
