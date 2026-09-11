# Local Development

This project runs locally on Minikube with the Podman driver and containerd.
The cluster script installs the full stack with Helm.

## Prerequisites

Install these tools:

- Minikube
- Podman
- `kubectl`
- Helm
- Google Cloud CLI, if you use BigQuery

Enter the devenv shell before you run the commands:

```bash
devenv shell
```

The script stores Kubernetes credentials in the ignored `.kubeconfig` file in the repository.
The script does not change your user kubeconfig.

## Start the cluster

```bash
cluster up
```

The script installs:

- KEDA
- k6 operator
- Istio
- MinIO
- OIDC provider
- PostgreSQL with pg_duckdb
- Valkey
- PostgREST
- Data Proxy

Check the cluster:

```bash
cluster
```

## Load BigQuery test data

Authenticate with Google Cloud:

```bash
gcloud auth application-default login
```

Seed the three test tables:

```bash
seed --project rj-ia-desenvolvimento
```

The producer also needs Google Cloud credentials inside Kubernetes.
Configure those credentials before you run a sync.

Run one sync:

```bash
kubectl -n data-proxy create job --from=cronjob/data-proxy-producer data-proxy-producer-manual
```

## Access the API

Add the local host name:

```text
127.0.0.1 data-proxy.local
```

Forward only the Data Proxy ingress:

```bash
kubectl -n istio-ingress port-forward svc/istio-ingressgateway 3111:80
```

Get a local token:

```bash
token
```

The token script gets the token from the OIDC provider inside the cluster.
The script does not forward the OIDC service.

See [Using the API](using.md) for request examples.

## Run the k6 load test

Run the e2e test first when you change local images, sync configuration, or fallback behavior:

```bash
cluster k6 e2e
```

Run the normal load profile:

```bash
cluster k6 load
```

Run the stepped stress profile:

```bash
cluster k6 stress
```

The load and stress commands clear local test state, run a full sync, and wait for local tables before VUs start. The normal load profile runs for at least five minutes. It reports separate source metrics in milliseconds:

```text
cache_duration_ms
postgrest_duration_ms
bigquery_duration_ms
load_request_failed
```

The load profile uses valid RLS routes for each test user. It also sends paired requests to BigQuery-only protocol partitions.

## Run the k6 e2e test

The e2e test triggers a full sync pipeline, seeds access policy rows, and validates local data, fallback, cache, and RLS behavior.

```bash
cluster k6 e2e
```

The command clears MinIO, Valkey, and PostgreSQL state before the test. It waits for the pipeline and prints the k6 summary.

## Stop the cluster

```bash
cluster down
```

If a Helm install fails, remove the cluster and start the cluster again:

```bash
cluster down
cluster up
```
