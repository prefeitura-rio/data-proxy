use lib.nu [fail local-kubeconfig NAMESPACE wrap-kubectl]

const LOCAL_ISSUER = 'http://keycloak.keycloak.svc.cluster.local:8080/realms/dev'
const LOCAL_CLIENT_SECRET = 'test-secret'

# Fetch an OIDC access token from a Keycloak realm and return it as a record.
def main [
    --issuer: string # Keycloak realm address, for example https://sso.example.com/realms/prod
    --client-id: string = 'api' # OAuth client ID
    --client-secret: string # Client secret; falls back to KEYCLOAK_CLIENT_SECRET, then to the local test secret
    --username: string # Use the password grant for this user
    --password: string # Password for the password grant
    --scope: string # Space-separated scopes to request
]: nothing -> record {
    let local = $issuer == null
    let realm = $issuer | default $LOCAL_ISSUER | str trim --right --char '/'
    let url = $'($realm)/protocol/openid-connect/token'
    let secret = $client_secret
    | default $env.KEYCLOAK_CLIENT_SECRET?
    | default (if $local { $LOCAL_CLIENT_SECRET })

    if $username != null and $password == null {
        fail '--password is required with --username'
    }

    let form = {
        grant_type: (if $username == null { 'client_credentials' } else { 'password' })
        client_id: $client_id
        client_secret: $secret
        username: $username
        password: $password
        scope: $scope
    }
    | compact

    let response = try {
        if $local {
            let fields = $form
            | items {|key, value| ['--data-urlencode' $'($key)=($value)'] }
            | flatten

            wrap-kubectl (local-kubeconfig) run data-proxy-token-client $'--namespace=($NAMESPACE)' --rm --attach --quiet --restart=Never --image=curlimages/curl:8.12.1 --command -- curl -sf -X POST $url ...$fields
            | from json
        } else {
            http post --content-type application/x-www-form-urlencoded $url $form
        }
    } catch {|err| fail $'Failed to fetch a token from ($url): ($err.msg)' }

    let token = $response | get --optional access_token

    if $token == null {
        fail $'The response from ($url) has no access_token'
    }

    let result = {
        token: $token
        token_type: $response.token_type?
        expires_in: $response.expires_in?
        scope: $response.scope?
        client_id: $client_id
        issuer: $realm
    }

    if $local {
        $result
        | insert port_forward 'kubectl -n istio-ingress port-forward svc/istio-ingressgateway 3111:80'
        | insert request $"curl -s http://localhost:3111/full_table -H 'Host: data-proxy.local' -H 'Accept-Profile: test' -H \"Authorization: Bearer $TOKEN\""
    } else {
        $result
    }
}
