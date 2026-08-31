#!/usr/bin/env bash
set -euo pipefail

helm lint helm/ -f helm/ci/test-values.yaml
helm lint helm/ -f helm/ci/test-values-ha.yaml
helm unittest helm/

for values in helm/ci/test-values.yaml helm/ci/test-values-ha.yaml; do
    helm template data-proxy helm/ -f "$values" |
        kubeconform \
            -strict \
            -summary \
            -schema-location default \
            -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
            -ignore-missing-schemas
done
