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

The k6 load test uses the OIDC provider and the Istio ingress gateway.
The test accesses the `pic` schema and all three synced tables.

Run one profile:

```bash
cluster k6 load-test smoke
cluster k6 load-test load
cluster k6 load-test stress
```

The profiles use these default loads:

- `smoke`: 1 VU for 30 seconds
- `load`: 10 VUs for 5 minutes
- `stress`: 50 VUs for 10 minutes

## Run the k6 e2e test

The e2e test triggers a full sync pipeline, seeds access policy rows, and
validates that all tables publish with fresh data and correct RLS filtering.

```bash
cluster k6 e2e
```

The command clears MinIO, Redis, and Postgres state before the test runs.
The command waits for the pipeline to complete and prints the k6 summary.

## Stop the cluster

```bash
cluster down
```

If a Helm install fails, remove the cluster and start the cluster again:

```bash
cluster down
cluster up
```
