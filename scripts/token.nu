use std/log

# Fetch a test OIDC token and print export and curl commands for local testing.
def main []: nothing -> nothing {
    try { kubectl config use-context data-proxy } catch { null }

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
        client_id=user-with-access
        -d
        client_secret=test-secret
    ]

    let token = try {
        kubectl ...$args
        | from json
        | get access_token
    } catch {|err|
        log error $"Failed to fetch token: ($err.msg)"
        exit 1
    }

    print $"export TOKEN='($token)'"
    print "
# Test RLS:
kubectl -n istio-ingress port-forward svc/istio-ingressgateway 3111:80 >/tmp/data-proxy-api-port-forward.log 2>&1 &"
    print $"curl -s http://localhost:3111/endpoint_participante_listagem -H 'Host: data-proxy.local' -H 'Accept-Profile: pic' -H \"Authorization: Bearer ($token)\""
}
