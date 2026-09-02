#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="data-proxy"
KUBECONFIG_FILE="$ROOT_DIR/.kubeconfig"
mkdir -p "$(dirname "$KUBECONFIG_FILE")"
MINIKUBE=(minikube --profile "$PROFILE")
KUBECTL=(kubectl --kubeconfig="$KUBECONFIG_FILE")

usage() {
    cat <<'EOF'
Usage: cluster [up|sync|e2e|k6|down|status]

Commands:
  up      Start Minikube and install the complete local stack.
  sync    Create a one-time Job from the producer CronJob.
  e2e     Run the full sync pipeline and validate with k6.
  k6      Run a k6 load test [smoke|load|stress].
  down    Remove the stack and the Minikube profile.
  status  Show Minikube and Kubernetes status.
EOF
}

wait_for_deployments() {
    local deployment reference namespace name
    for reference in "$@"; do
        namespace="${reference%%/*}"
        name="${reference#*/}"
        "${KUBECTL[@]}" -n "$namespace" rollout status "deployment/$name" --timeout=15m
    done
}

wait_for_statefulsets() {
    local reference namespace name
    for reference in "$@"; do
        namespace="${reference%%/*}"
        name="${reference#*/}"
        "${KUBECTL[@]}" -n "$namespace" rollout status "statefulset/$name" --timeout=15m
    done
}

up() {
    if ! KUBECONFIG="$KUBECONFIG_FILE" "${MINIKUBE[@]}" status >/dev/null 2>&1; then
        KUBECONFIG="$KUBECONFIG_FILE" "${MINIKUBE[@]}" start --driver=podman --container-runtime=containerd --cpus=6 --memory=12288 --disk-size=40g
    fi
    KUBECONFIG="$KUBECONFIG_FILE" "${MINIKUBE[@]}" update-context

    minikube image build -p "$PROFILE" -t data-proxy:local -f "$ROOT_DIR/Dockerfile" "$ROOT_DIR"
    minikube image build -p "$PROFILE" -t data-proxy-postgres:local -f "$ROOT_DIR/Dockerfile.postgres" "$ROOT_DIR"

    helm repo add kedacore https://kedacore.github.io/charts >/dev/null 2>&1 || true
    helm repo add istio https://istio-release.storage.googleapis.com/charts >/dev/null 2>&1 || true
    helm repo add bedag https://bedag.github.io/helm-charts/ >/dev/null 2>&1 || true
    helm repo add grafana https://grafana.github.io/k6-operator >/dev/null 2>&1 || true
    helm repo update
    helm dependency build "$ROOT_DIR/helm"

    helm install keda kedacore/keda --namespace keda --create-namespace --version 2.18.0
    helm install k6-operator grafana/k6-operator --namespace k6-operator-system --create-namespace --version 4.6.0
    helm install istio-base istio/base --namespace istio-system --create-namespace --version 1.27.1
    helm install istiod istio/istiod --namespace istio-system --version 1.27.1
    helm install istio-ingressgateway istio/gateway --namespace istio-ingress --create-namespace --version 1.27.1 --set service.type=NodePort
    helm install data-proxy-gateway bedag/raw --namespace istio-system --values "$ROOT_DIR/scripts/values/gateway.yaml"
    helm install minio oci://registry-1.docker.io/cloudpirates/minio --namespace data-proxy --create-namespace --version 0.13.3 --values "$ROOT_DIR/scripts/values/minio.yaml"
    helm install mock-oauth2-server bedag/raw --namespace data-proxy --values "$ROOT_DIR/scripts/values/mock-oauth2.yaml"
    helm install webdis bedag/raw --namespace data-proxy --values "$ROOT_DIR/scripts/values/webdis.yaml"

    if [[ -n "${GOOGLE_APPLICATION_CREDENTIALS:-}" ]]; then
        "${KUBECTL[@]}" -n data-proxy create secret generic gcp-key --from-file=key.json="$GOOGLE_APPLICATION_CREDENTIALS" --dry-run=client -o yaml | "${KUBECTL[@]}" apply -f -
    fi

    helm install data-proxy "$ROOT_DIR/helm" --namespace data-proxy --values "$ROOT_DIR/scripts/values/data-proxy.yaml"

    wait_for_deployments keda/keda-operator keda/keda-operator-metrics-apiserver keda/keda-admission-webhooks k6-operator-system/k6-operator-controller-manager istio-system/istiod istio-ingress/istio-ingressgateway data-proxy/minio data-proxy/mock-oauth2-server data-proxy/webdis data-proxy/data-proxy-postgrest data-proxy/data-proxy-swagger-ui

    wait_for_statefulsets data-proxy/data-proxy-duckdb data-proxy/data-proxy-valkey
    "${KUBECTL[@]}" get pods -A
    "${KUBECTL[@]}" get scaledjobs -n data-proxy
}

sync() {
    local job_name
    job_name="data-proxy-producer-manual-$(date +%s)"
    "${KUBECTL[@]}" -n data-proxy create job "$job_name" --from=cronjob/data-proxy-producer
    "${KUBECTL[@]}" -n data-proxy wait --for=condition=complete "job/$job_name" --timeout=2h
    "${KUBECTL[@]}" -n data-proxy logs "job/$job_name"
}

k6() {
    local profile="${1:-smoke}"
    case "$profile" in
    smoke | load | stress) ;;
    *)
        echo "Usage: cluster k6 [smoke|load|stress]" >&2
        return 2
        ;;
    esac
    "${KUBECTL[@]}" -n data-proxy create configmap data-proxy-k6 --from-file=run.ts=k6/run.ts --dry-run=client -o yaml | "${KUBECTL[@]}" apply -f -
    "${KUBECTL[@]}" -n data-proxy delete testrun data-proxy-load --ignore-not-found
    sed "s/value: smoke/value: $profile/" k6/run.yaml | "${KUBECTL[@]}" apply -f -
    "${KUBECTL[@]}" -n data-proxy get testrun data-proxy-load -w
}

e2e() {
    sync
    "${KUBECTL[@]}" -n data-proxy create configmap data-proxy-e2e --from-file=e2e.ts=k6/e2e.ts --dry-run=client -o yaml | "${KUBECTL[@]}" apply -f -
    "${KUBECTL[@]}" -n data-proxy delete testrun data-proxy-e2e --ignore-not-found
    "${KUBECTL[@]}" apply -f k6/e2e.yaml
    "${KUBECTL[@]}" -n data-proxy wait --for=condition=complete testrun/data-proxy-e2e --timeout=15m
    local e2e_pod
    e2e_pod=$("${KUBECTL[@]}" -n data-proxy get pods -l k6-test-run-id=data-proxy-e2e -o jsonpath='{.items[0].metadata.name}')
    "${KUBECTL[@]}" -n data-proxy logs "$e2e_pod"
}

down() {
    helm uninstall data-proxy -n data-proxy --no-hooks 2>/dev/null || true
    helm uninstall webdis -n data-proxy 2>/dev/null || true
    helm uninstall mock-oauth2-server -n data-proxy 2>/dev/null || true
    helm uninstall minio -n data-proxy 2>/dev/null || true
    helm uninstall data-proxy-gateway -n istio-system 2>/dev/null || true
    helm uninstall istio-ingressgateway -n istio-ingress 2>/dev/null || true
    helm uninstall istiod -n istio-system 2>/dev/null || true
    helm uninstall istio-base -n istio-system 2>/dev/null || true
    helm uninstall k6-operator -n k6-operator-system 2>/dev/null || true
    helm uninstall keda -n keda 2>/dev/null || true
    "${MINIKUBE[@]}" delete
}

status() {
    KUBECONFIG="$KUBECONFIG_FILE" "${MINIKUBE[@]}" status
    "${KUBECTL[@]}" get pods -A
    "${KUBECTL[@]}" get scaledjobs -n data-proxy 2>/dev/null || true
}

case "${1:-up}" in
up) up ;;
sync) sync ;;
e2e) e2e ;;
k6) k6 "${2:-smoke}" ;;
down) down ;;
status) status ;;
*)
    usage >&2
    exit 2
    ;;
esac
