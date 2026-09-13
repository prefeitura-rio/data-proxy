# Backups

A sync rebuilds application tables from BigQuery. It does not rebuild access grants. The chart backs up each `<schema>.access_policy` table.

## Operation

Set `backup.enabled` to create one CronJob per configured schema. Each job exports `access_policy` as CSV, encrypts it with [age](https://github.com/FiloSottile/age), and uploads it to:

```text
<backup.prefix>/<schema>/<date>.csv.age
```

The chart default prefix is:

```text
backups/access_policy
```

The chart does not store the age private key. Keep it outside the cluster.

## Configuration

```yaml
backup:
  enabled: true
  ageRecipient: "age1..."
  password: "..."
  schedule: "0 3 * * *"
```

`ageRecipient` is the encryption recipient. `password` is the PostgreSQL backup-role password, not an age password.

The default schedule is daily at 03:00 UTC. Configure bucket lifecycle rules for retention; the chart does not delete backup objects.

## Verify a backup

Download first, then decrypt:

```bash
aws s3 cp \
  "s3://<bucket>/backups/access_policy/<schema>/<date>.csv.age" \
  backup.csv.age

age --decrypt \
  --identity key.txt \
  --output verified.csv \
  backup.csv.age
```

```bash
head -1 verified.csv
wc -l verified.csv
```

## Restore

1. Decrypt into a reviewed local CSV.
2. Load it into a temporary table.
3. Compare it with `<schema>.access_policy`.
4. Apply reviewed rows only.

Do not load an unreviewed backup into a live policy table.

---

[← Previous](metrics.md) · [Home](../README.md) · [Next →](development.md)
