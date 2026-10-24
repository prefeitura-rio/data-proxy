use lib.nu [fail read-data]

const RULES = 'helm/files/config/rbac.toml'

# Return the names of the roles in the rules.
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

# Return the document that Keycloak reads in Realm settings > Action > Partial import. It creates the client of the
# console with one client role for each role of the rules and the three claims that the data proxy reads.
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

# Return the attribute that a realm with the declarative user profile needs for the claim `schemas`. Without it Keycloak
# drops the user attribute, and the mapper puts nothing in the token. Add it to the `attributes` of the user profile.
export def schemas-attribute []: nothing -> record {
    {
        name: schemas
        displayName: 'Data proxy schemas'
        validations: {}
        annotations: {}
        permissions: {
            view: [admin]
            edit: [admin]
        }
        multivalued: true
    }
}

# Return a claim as a list, whether the token has one text value or a list of them.
def as-list [claim: oneof<string, list<string>, nothing>]: nothing -> list<string> {
    [$claim] | flatten | compact
}

# Return the problems of the claims of an access token: a wrong issuer or audience, a role that the rules lack, and a
# missing or empty `schemas` or `preferred_username` claim. An empty list means that the token is right.
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
        (if (as-list ($claims | get --optional schemas)) == [] { 'schemas is missing or empty, so the token reads nothing' })
        (if ($claims | get --optional preferred_username) in [null ''] { 'preferred_username is missing' })
    ]
    | compact
}

# Return the claims of a JSON Web Token, without a check of the signature.
export def claims-of [token: string]: nothing -> record {
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

# Print the partial import document for the console client. The roles come from rbac.toml.
def "main client" [
    --host: string # The public host of the API and the console, such as preview.data-proxy.dados.rio
    --audience: string = data-proxy # The audience of the gateway (ingress.auth.audience)
    --client-id: string = console-web # The client ID that the console uses (console.oidc.clientId)
]: nothing -> nothing {
    if $host == null {
        fail '--host is required'
    }

    partial-import (read-data $RULES) {id: $client_id, host: $host, audience: $audience} | to json --indent 2 | print
}

# Print the attribute to add to the user profile of the realm, as JSON.
def "main user-profile-attribute" []: nothing -> nothing {
    schemas-attribute | to json --indent 2 | print
}

# Check the claims of an access token against the roles of rbac.toml. It fails with the list of the problems.
def "main check" [
    token: string # The access token of a user of the console client
    --issuer: string # The issuer that the gateway expects (ingress.auth.issuer)
    --audience: string = data-proxy # The audience that the gateway expects (ingress.auth.audience)
]: nothing -> nothing {
    if $issuer == null {
        fail '--issuer is required'
    }

    let found = token-problems (claims-of $token) {
        issuer: $issuer
        audience: $audience
        roles: (role-names (read-data $RULES))
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
