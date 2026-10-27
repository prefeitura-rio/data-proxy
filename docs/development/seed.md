# Seed BigQuery data

The seed command creates every E2E fixture table, including the mutable snapshot fixture. Rerun it after a fixture schema change, then synchronize the local release:

```bash
gcloud auth application-default login
seed --project rj-ia-desenvolvimento
cluster sync
```

Run one DBOS sync through the E2E trigger Job:

```bash
cluster k6 e2e
```

The temporary e2e Job mounts the standalone trigger at `/scripts`. It isn't part of the sync image.

<!-- nav:start -->
---

[Previous: CI pipeline](ci.md) | [Next: Access the API](api.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
