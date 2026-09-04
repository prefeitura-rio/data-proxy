#!/usr/bin/env nu

use http-nu/router *
use std/log

let CONFIG = open /config/oidc.nuon

# Encode a string as base64url without padding.
def b64url [s: string]: nothing -> string {
    $s | encode base64 --url | str replace --all "=" ""
}

# Decode a base64url string to UTF-8 text, restoring padding first.
def b64url-decode [s: string]: nothing -> string {
    let padding_count = (4 - ($s | str length) mod 4) mod 4
    let pad = if $padding_count > 0 {
        1..$padding_count | each { "=" } | str join ""
    } else { "" }
    $"($s)($pad)" | decode base64 --url | decode utf-8
}

# Sign a JWT payload with HS256 using the configured secret.
def sign-jwt [payload: record]: nothing -> string {
    let header = {alg: "HS256", typ: "JWT"} | to json
    let payload_json = $payload | to json

    let header_b64 = b64url $header
    let payload_b64 = b64url $payload_json
    let signing_input = $"($header_b64).($payload_b64)"

    let sig = (
        $signing_input
        | ^openssl dgst -sha256 -hmac $CONFIG.secret -binary
        | encode base64 --url
        | str replace --all "=" ""
    )

    $"($signing_input).($sig)"
}

# Decode the payload section of a JWT and parse it as a record.
def decode-jwt-payload [token: string]: nothing -> record {
    $token | split row "." | get 1 | b64url-decode $in | from json
}

# Build a JSON HTTP response with the given body and optional status code.
def json-response [body: record, status?: int]: nothing -> string {
    $body | to json | metadata set {
        merge {
            'http.response': {
                status: ($status | default 200)
                headers: {"Content-Type": "application/json"}
            }
        }
    }
}

# Build the OpenID Connect discovery document.
def discovery-document []: nothing -> record {
    {
        issuer: $CONFIG.issuer
        token_endpoint: $"($CONFIG.issuer)/token"
        userinfo_endpoint: $"($CONFIG.issuer)/userinfo"
        jwks_uri: $"($CONFIG.issuer)/jwks.json"
        revocation_endpoint: $"($CONFIG.issuer)/revoke"
        response_types_supported: ["token" "code"]
        grant_types_supported: ["client_credentials" "authorization_code"]
        token_endpoint_auth_methods_supported: ["client_secret_post"]
        scopes_supported: ["openid" "profile"]
        claims_supported: [
            "sub"
            "role"
            "preferred_username"
            "schemas"
            "iss"
            "aud"
            "exp"
            "iat"
        ]
    }
}

# Issue an access token for a configured client.
def issue-token [body: record]: nothing -> string {
    let client_id = $body.client_id? | default ""
    let client_secret = $body.client_secret? | default ""

    if $client_id not-in ($CONFIG.clients | columns) {
        log warning $"token rejected: unknown client ($client_id)"
        return (
            json-response {error: "invalid_client", error_description: "unknown client"} 401
        )
    }

    if $client_secret != ($CONFIG.clients | get $client_id | get client_secret) {
        log warning $"token rejected: bad secret for client ($client_id)"
        return (
            json-response {error: "invalid_client", error_description: "client authentication failed"} 401
        )
    }

    let claims = $CONFIG.clients | get $client_id | get claims
    let now = (date now | into int) // 1_000_000_000

    let payload = $claims | merge {
        iss: $CONFIG.issuer
        aud: $CONFIG.audience
        iat: $now
        exp: ($now + $CONFIG.token_ttl)
    }

    log info $"token issued: client=($client_id) sub=($payload.sub)"
    json-response {
        access_token: (sign-jwt $payload)
        token_type: "Bearer"
        expires_in: $CONFIG.token_ttl
    }
}

# Return the claims from a Bearer token in the Authorization header.
def handle-userinfo [req: record]: nothing -> string {
    let auth = $req.headers | get authorization? | default ""

    if not ($auth | str starts-with "Bearer ") {
        log warning "userinfo rejected: missing or malformed Authorization header"
        return (json-response {error: "invalid_token"} 401)
    }

    $auth
    | str replace "Bearer " ""
    | decode-jwt-payload $in
    | reject iat
    | reject exp
    | reject iss
    | reject aud
    | json-response $in
}

{|req| dispatch $req [
    (route {method: "GET", path: "/.well-known/openid-configuration"} {|req ctx|
        log info "served discovery document"
        json-response (discovery-document)
    })
    (route {method: "GET", path: "/jwks.json"} {|req ctx|
        json-response {keys: []}
    })
    (route {method: "POST", path: "/token"} {|req ctx|
        issue-token ($in | from url)
    })
    (route {method: "GET", path: "/userinfo"} {|req ctx|
        handle-userinfo $req
    })
    (route {method: "POST", path: "/revoke"} {|req ctx|
        log info "token revoked"
        null | metadata set {merge {'http.response': {status: 200}}}
    })
    (route true {|req ctx|
        log warning $"no route: ($req.method) ($req.path)"
        json-response {error: "not_found", error_description: $req.path} 404
    })
] }
