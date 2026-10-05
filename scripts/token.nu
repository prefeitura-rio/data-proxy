use std/log
use ./lib.nu [fail local-kubeconfig wrap-kubectl]

# Fetch a test OIDC token and print export and curl commands for local testing.
def main []: nothing -> nothing {
    let kubecfg = local-kubeconfig
    let args = [
        run
        data-proxy-token-client
        --namespace=data-proxy
        --rm
        --stdin=false
        --restart=Never
        --image=curlimages/curl:8.12.1
        --command
        --
        curl
        -sf
        -X
        POST
        http://oidc:8080/token
        -H
        "Content-Type: application/x-www-form-urlencoded"
        -d
        grant_type=client_credentials
        -d
        client_id=user
        -d
        client_secret=test-secret
    ]

    let token = try {
        wrap-kubectl $kubecfg ...$args
        | from json
        | get access_token
    } catch {|err| fail $"Failed to fetch token: ($err.msg)" {
            command: main
            span: (metadata $args).span
        } }

    print $"export TOKEN='($token)'"
    print "
# Test RLS:
kubectl -n istio-ingress port-forward svc/istio-ingressgateway 3111:80 >/tmp/data-proxy-api-port-forward.log 2>&1 &"
    print $"curl -s http://localhost:3111/full_table -H 'Host: data-proxy.local' -H 'Accept-Profile: test' -H \"Authorization: Bearer ($token)\""
}
