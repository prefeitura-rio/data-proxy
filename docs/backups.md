# Backups

## Scope

The backup covers only `rls.access_policy`. A sync rebuilds every BigQuery table. A sync does not rebuild `rls.access_policy`. This table exists only through grants written by PostgREST. No other source holds this data.

## How it works

A `CronJob` runs these steps. The flag `backup.enabled` turns it on. The default value is off.

1. The job connects to Postgres as a `backup` role. This role has `SELECT` on `rls.access_policy` only.
2. The job runs `pg_dump` on this one table, in custom format.
3. The job encrypts the dump with [`age`](https://github.com/FiloSottile/age). The setting `backup.ageRecipient` holds the public key.
4. The job uploads the encrypted file to `gcs.bucket`, under `backup.prefix`. The file name is the date, for example `2026-01-15.age`.

The chart does not store the `age` private key. Keep the private key outside the cluster. Only the holder of the private key can decrypt a backup.

## Enabling the backup

Set these values:

```yaml
backup:
  enabled: true
  ageRecipient: "age1..." # public key from `age-keygen`
  password: "..." # required unless backup.existingSecret is set
```

Store the private key outside the cluster.

The chart creates the `backup` role through the same init scripts as every other role. These scripts run once, at the first startup of an empty volume. If the cluster already runs, create the role by hand:

```sql
CREATE ROLE backup NOINHERIT LOGIN PASSWORD '...';
GRANT USAGE ON SCHEMA rls TO backup;
GRANT SELECT ON rls.access_policy TO backup;
```

## Restoring a backup

A restore is a manual task. Follow these steps in order.

1. Download the backup file:

   ```bash
   aws s3 cp "s3://${GCS_BUCKET}/backups/access_policy/2026-01-15.age" backup.age --endpoint-url https://storage.googleapis.com
   ```

2. Decrypt the file with the private key:

   ```bash
   age --decrypt --identity key.txt --output backup.dump backup.age
   ```

3. Check the dump contents:

   ```bash
   pg_restore --list backup.dump
   ```

4. Restore the dump into a new table or a new schema.
5. Compare the restored data against the live table.
6. Apply only the rows you need.

Do not run `pg_restore` directly against the live `rls.access_policy` table.
