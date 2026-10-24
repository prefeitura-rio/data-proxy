use std/assert
use ../keycloak.nu [claims-of partial-import role-names schemas-attribute token-problems]

const RULES = {
    roles: [
        {
            name: dp_api
            description: 'Read the data'
            permissions: [read_api]
        }
        {
            name: dp_admin
            description: 'Run schemas'
            inherits: [dp_api]
            permissions: [start_sync]
        }
    ]
}

const GOOD = {
    iss: 'https://sso.example.test/realms/prod'
    aud: [account data-proxy]
    role: dp_admin
    schemas: [test]
    preferred_username: ana
}

const LIST_AS_TEXT = 'role [dp_admin] is a list that Keycloak wrote as text. Map the role from a user attribute, not from a client role'

const EXPECTED = {
    issuer: 'https://sso.example.test/realms/prod'
    audience: data-proxy
    roles: [dp_api dp_admin]
}

# Verify that the client roles are the roles of the rules, with their descriptions.
export def "test partial-import-has-one-client-role-for-each-rule-role" []: nothing -> nothing {
    let document = partial-import $RULES {id: console-web, host: api.example.test, audience: data-proxy}

    assert equal $document.roles.client.console-web [
        {name: dp_api, description: 'Read the data'}
        {name: dp_admin, description: 'Run schemas'}
    ]
    assert equal (role-names $RULES) [dp_api dp_admin]
}

# Verify the redirect addresses and the mappers of the client.
export def "test partial-import-sets-the-callback-and-the-three-claims" []: nothing -> nothing {
    let client = partial-import $RULES {id: console-web, host: api.example.test, audience: data-proxy}
    | get clients
    | first

    assert equal $client.redirectUris ['https://api.example.test/console/auth/callback']
    assert equal $client.webOrigins ['https://api.example.test']
    assert equal $client.attributes.'post.logout.redirect.uris' 'https://api.example.test/console/auth/login'
    assert equal ($client.protocolMappers | get name) [role schemas audience]
    assert equal ($client.protocolMappers | where name == audience | first | get config.'included.custom.audience') data-proxy
    assert equal ($client.protocolMappers | where name == role | first | get config.'claim.name') role
}

# Verify each problem that a token can have, and that a good token has none.
export def "test token-problems-names-each-wrong-claim" []: nothing -> nothing {
    let cases = [
        [name claims expected];
        ['good token' $GOOD []]
        ['audience as text' ($GOOD | update aud data-proxy) []]
        ['schemas as text' ($GOOD | update schemas test) []]
        ['wrong issuer' ($GOOD | update iss 'https://other.test') ['iss is https://other.test, not https://sso.example.test/realms/prod']]
        ['wrong audience' ($GOOD | update aud [account]) ['aud lacks data-proxy']]
        ['no role' ($GOOD | reject role) ['role is missing']]
        ['unknown role' ($GOOD | update role dp_root) ['role dp_root is not a role of rbac.toml: dp_api, dp_admin']]
        ['two roles' ($GOOD | update role [dp_api dp_admin]) ['role is not one text value']]
        ['list as text' ($GOOD | update role '[dp_admin]') [$LIST_AS_TEXT]]
        ['no schemas' ($GOOD | update schemas []) ['schemas is missing or empty, so the token reads nothing']]
        ['no user name' ($GOOD | reject preferred_username) ['preferred_username is missing']]
    ]

    for case in $cases {
        assert equal (token-problems $case.claims $EXPECTED) $case.expected $case.name
    }
}

# Verify that the claims come out of the payload of a token.
export def "test claims-of-reads-the-payload" []: nothing -> nothing {
    let payload = {role: dp_api, aud: [data-proxy]} | to json --raw | encode base64 --url --nopad
    let token = $'e30.($payload).signature'

    assert equal (claims-of $token) {role: dp_api, aud: [data-proxy]}
}

# Verify that the attribute is for admins only and takes several values, like the mapper of the client expects.
export def "test schemas-attribute-takes-several-values-and-only-admins-edit-it" []: nothing -> nothing {
    let attribute = schemas-attribute

    assert equal $attribute.name schemas
    assert equal $attribute.multivalued true
    assert equal $attribute.permissions {view: [admin], edit: [admin]}
}
