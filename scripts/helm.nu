const CRD_SCHEMA = 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'

# Run the Helm unit tests, then validate the rendered manifests with kubeconform.
def main []: nothing -> nothing {
    helm unittest helm/

    helm template data-proxy helm/ -f helm/ci/test-values.yaml
    | from yaml
    | compact
    | each {|doc|
        match $doc.kind {
            "Cluster" => ($doc | upsert spec.instances 1)
            _ => $doc
        }
        | to json --raw
    }
    | str join "\n---\n"
    | kubeconform -strict -summary -ignore-missing-schemas -schema-location default -schema-location $CRD_SCHEMA
}
