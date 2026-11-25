use lib.nu [fail ]


const RULES = 'helm/files/config/rbac.toml'

# Return the names of the roles in the rules.
@example "Read client role names" { role-names {roles: [{name: dp_api}]} } --result [dp_api]
export def role-names [rules: record]: nothing -> list<string> {
    $rules.roles | get name
}

# Return a protocol mapper that sets an OpenID Connect claim in the access token and the ID token.
def mapper [name: string, kind: string, config: record]: nothing -> record {
    {
        name: $name
        protocol: openid-connect
        protocolMapper: $kind
        config: ({
            'access.token.claim': 'true'
            'id.token.claim': 'true'
            'userinfo.token.claim': 'true'
        } | merge $config)
    }
}

# Return the Keycloak client import document with the configured roles and claims.
@example "Generate a console client import" { partial-import {roles: [{name: dp_api}]} {id: 'data-proxy-console-web', host: 'proxy.example.org', audience: 'data-proxy'} | get clients }
export def partial-import [
    rules: record
    client: record<id: string, host: string, audience: string>
]: nothing -> record {
    let origin = $'https://($client.host)'

    {
        clients: [
            {
                clientId: $client.id
                name: $client.id
                enabled: true
                protocol: openid-connect
                publicClient: false
                serviceAccountsEnabled: false
                standardFlowEnabled: true
                directAccessGrantsEnabled: false
                redirectUris: [$'($origin)/console/auth/callback']
                webOrigins: [$origin]
                attributes: {'post.logout.redirect.uris': $'($origin)/console/auth/login'}
                defaultClientScopes: [basic web-origins acr roles profile email]
                protocolMappers: [
                    (mapper role oidc-usermodel-client-role-mapper {
                        'usermodel.clientRoleMapping.clientId': $client.id
                        'claim.name': role
                        'jsonType.label': String
                        multivalued: 'false'
                    })
                    (mapper schemas oidc-usermodel-attribute-mapper {
                        'user.attribute': schemas
                        'claim.name': schemas
                        'jsonType.label': String
                        multivalued: 'true'
                        'aggregate.attrs': 'false'
                    })
                    (mapper projects oidc-usermodel-attribute-mapper {
                        'user.attribute': projects
                        'claim.name': projects
                        'jsonType.label': String
                        multivalued: 'true'
                        'aggregate.attrs': 'false'
                    })
                    {
                        name: audience
                        protocol: openid-connect
                        protocolMapper: oidc-audience-mapper
                        config: {
                            'included.custom.audience': $client.audience
                            'access.token.claim': 'true'
                            'id.token.claim': 'false'
                            'userinfo.token.claim': 'false'
                        }
                    }
                ]
            }
        ]
        roles: {
            client: {
                ($client.id): ($rules.roles | select name description)
            }
        }
    }
}

# Return a user-profile attribute definition for a token claim.
def claim-attribute [name: string, display_name: string]: nothing -> record {
    {
        name: $name
        displayName: $display_name
        validations: {}
        annotations: {}
        permissions: {
            view: [admin]
            edit: [admin]
        }
        multivalued: true
    }
}

# Return the user-profile attribute definition for the schemas claim.
@example "Describe the schemas claim" { schemas-attribute | get name } --result 'schemas'
export def schemas-attribute []: nothing -> record {
    claim-attribute schemas 'Data proxy schemas'
}

# Return the user-profile attribute definition for the projects claim.
@example "Describe the projects claim" { projects-attribute | get name } --result 'projects'
export def projects-attribute []: nothing -> record {
    claim-attribute projects 'Data proxy projects'
}

# Return a claim as a list, whether the token has one text value or a list of them.
def as-list [claim: oneof<string, list<string>, nothing>]: nothing -> list<string> {
    [$claim] | flatten | compact
}

# Return access-token claim errors for issuer, audience, role, schema access and username.
@example "Validate access-token claims" { token-problems {iss: 'https://id.example.org', aud: 'data-proxy', role: dp_api, schemas: ['default.orders'], preferred_username: reader} {issuer: 'https://id.example.org', audience: 'data-proxy', roles: [dp_api]} } --result []
export def token-problems [
    claims: record
    expected: record<issuer: string, audience: string, roles: list<string>>
]: nothing -> list<string> {
    let issuer = $claims | get --optional iss
    let role = $claims | get --optional role

    let role_problem = match ($role | describe) {
        'nothing' => 'role is missing'
        'string' => (if $role not-in $expected.roles {
            if $role =~ '^\[.*\]$' {
                $'role ($role) is a list that Keycloak wrote as text. Map the role from a user attribute, not from a client role'
            } else {
                $'role ($role) is not a role of rbac.toml: ($expected.roles | str join ", ")'
            }
        })
        _ => 'role is not one text value'
    }

    [
        (if $issuer != $expected.issuer { $'iss is ($issuer | default missing), not ($expected.issuer)' })
        (if $expected.audience not-in (as-list ($claims | get --optional aud)) { $'aud lacks ($expected.audience)' })
        $role_problem
        (if (as-list ($claims | get --optional schemas) | append (as-list ($claims | get --optional projects))) == [] { 'schemas and projects are missing or empty, so the token reads nothing' })
        (if ($claims | get --optional preferred_username) in [null ''] { 'preferred_username is missing' })
    ]
    | compact
}

# Return the claims of a JSON Web Token, without a check of the signature.
@example "Decode a token payload" { let payload = ('{"sub":"reader"}' | encode base64 --url --nopad); decode-claims $"header.($payload).signature" | get sub } --result 'reader'
export def decode-claims [token: string]: nothing -> record {
    let parts = $token | parse '{header}.{payload}.{signature}'

    if ($parts | is-empty) {
        fail 'The token is not a JSON Web Token with three parts'
    }

    try {
        $parts | first | get payload | decode base64 --url --nopad | decode utf-8 | from json
    } catch {|err|
        fail $'Cannot read the payload of the token: ($err.msg)'
    }
}

# Print the Keycloak client import document using the roles from rbac.toml.
def "main client" [
    --host: string # The public host of the API and the console, such as data-proxy.dados.rio
    --audience: string = data-proxy # The audience of the gateway (ingress.auth.audience)
    --client-id: string = data-proxy-console-web # The client ID that the console uses (console.oidc.clientId)
]: nothing -> nothing {
    if $host == null {
        fail '--host is required'
    }

    partial-import (open $RULES) {id: $client_id, host: $host, audience: $audience} | to json --indent 2 | print
}

# Print the attributes to add to the user profile of the realm, as JSON.
def "main user-profile-attribute" []: nothing -> nothing {
    [(schemas-attribute) (projects-attribute)] | to json --indent 2 | print
}

# Validate token claims against the expected issuer, audience and configured roles.
def "main check" [
    token: string # The access token of a user of the console client
    --issuer: string # The issuer that the gateway expects (ingress.auth.issuer)
    --audience: string = data-proxy # The audience that the gateway expects (ingress.auth.audience)
]: nothing -> nothing {
    if $issuer == null {
        fail '--issuer is required'
    }

    let found = token-problems (decode-claims $token) {
        issuer: $issuer
        audience: $audience
        roles: (role-names (open $RULES))
    }

    if ($found | is-not-empty) {
        fail $"The token has problems:\n($found | str join "\n")"
    }

    print 'The token has the claims that the data proxy expects.'
}

# Print the Keycloak subcommands.
def main []: nothing -> nothing {
    print 'Use `client` to print the import document or `check` to check a token.'
}
