# Setup

These sections list what you install, how the Python project is laid out, and how to stop the cluster.

## Prerequisites

Install k3d, Podman, `kubectl`, Helm, and Google Cloud CLI when using BigQuery. Ensure the existing Podman network has DNS enabled and the Podman Docker-compatible socket variables are available in the shell that runs k3d.

```bash
devenv --profile default shell
gcloud auth application-default login
cluster up
```

The local synchronization configuration uses BigQuery. `cluster up` stops before Helmfile when application-default credentials are absent.

The k3d configuration mounts `k3d/.tmp/duckdb` into every local node. Helmfile applies the local-only `k3d-rwx` static ReadWriteMany (RWX) storage manifest for the shared `data-proxy-duckdb` volume, which contains the DuckDB secrets.

The script writes the k3d credentials to the ignored `k3d/.kubeconfig`. It doesn't change the user kubeconfig.

Apply the current immutable local image tag without rebuilding images:

```bash
cluster sync
```

Use `cluster sync --build` to build and push fresh local images under a new immutable tag before applying them.

Installed services include Kubernetes Event-driven Autoscaling (KEDA), k6, Istio, SeaweedFS, OIDC, PostgreSQL with the pg_duckdb extension, Valkey, PostgREST, and Data Proxy.

```bash
cluster
```

## Python project

`sync/` is one flat Python project: `sync/pyproject.toml`, the lock file `sync/uv.lock`, the environment `sync/.venv`, the code in `sync/src/sync`, and the tests in `sync/tests`.

`ruff`, `basedpyright`, `pyrefly`, `complexipy`, `vulture` and `pytest` all read their settings from that one `pyproject.toml`.

`uv sync` prepares the environment. The sync image runs `uv sync --frozen --no-dev --no-install-project` and sets `PYTHONPATH=/app/src`. The chart starts the worker with `python -m sync.dbos.main`.

## Stop the cluster

```bash
cluster down
```

If Helm installation fails, recreate the cluster:

```bash
cluster down
cluster up
```

---

<!-- nav:start -->
---

[Previous: Local Development](index.md) | [Next: Tests](tests.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
