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
It does not change your user kubeconfig.

## Start the cluster

```bash
cluster up
```

The script installs:

- KEDA
- k6 operator
- Istio
- MinIO
- Mock OAuth2
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
seed-data --project rj-ia-desenvolvimento
```

The producer also needs Google Cloud credentials inside Kubernetes.
Configure those credentials before you run a sync.

Run one sync:

```bash
cluster sync
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
get-token
```

The token script gets the token from the mock OAuth2 service inside the cluster.
It does not forward the OAuth2 service.

See [Using the API](using.md) for request examples.

## Run the k6 test

The k6 test uses the mock token service and the Istio ingress gateway.
It tests the `pic` schema and all three synced tables.

Run one profile:

```bash
cluster k6 smoke
cluster k6 load
cluster k6 stress
```

The profiles use these default loads:

- `smoke`: 1 VU for 30 seconds
- `load`: 10 VUs for 5 minutes
- `stress`: 50 VUs for 10 minutes

## Stop the cluster

```bash
cluster down
```

If a Helm install fails, remove the cluster and start it again:

```bash
cluster down
cluster up
```
