use lib.nu [fail]

const CRD_SCHEMA = 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'

# Run the Helm unit tests, then validate the rendered manifests with kubeconform.
def main []: nothing -> nothing {
    helm unittest helm/

    let manifest = helm template data-proxy helm/ -f helm/ci/test-values.yaml
    let documents = try {
        $manifest | from yaml
    } catch {|err| fail $'Could not parse the rendered Helm manifest: ($err.msg)' {
            command: main
            span: (metadata $manifest).span
        } }

    $documents
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
