# Fetch a dev OAuth2 token from the mock server inside the cluster
# and print an `export TOKEN='...'` line suitable for `eval`.

^kubectl config use-context data-proxy | ignore

let token = (^kubectl run data-proxy-token-client
  --namespace data-proxy
  --rm
  --stdin=false
  --restart=Never
  --image=curlimages/curl:8.12.1
  --command --
  curl -sf -X POST http://mock-oauth2-server:8080/default/token
  -H "Content-Type: application/x-www-form-urlencoded"
  -d "grant_type=client_credentials"
  -d "client_id=dev"
  | from json
  | get access_token)

print $"export TOKEN='($token)'"
print ""
print "# Test RLS:"
print "kubectl -n istio-ingress port-forward svc/istio-ingressgateway 3111:80 >/tmp/data-proxy-api-port-forward.log 2>&1 &"
print $"curl -s http://localhost:3111/endpoint_participante_listagem -H 'Host: data-proxy.local' -H 'Accept-Profile: pic' -H \"Authorization: Bearer ($token)\""
