const SCHEMA_LOCATION = 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'

helm lint helm/ -f helm/ci/test-values.yaml
helm lint helm/ -f helm/ci/test-values-ha.yaml
helm unittest helm/

for values in [helm/ci/test-values.yaml helm/ci/test-values-ha.yaml] {
    helm template data-proxy helm/ -f $values
    | kubeconform -strict -summary -ignore-missing-schemas -schema-location default -schema-location $SCHEMA_LOCATION
    | print $in
}
