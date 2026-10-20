use std/log
use ./lib.nu [fail]

# The fields that a key of each type needs, next to its kid.
const KEY_FIELDS = {
    RSA: [n e]
    EC: [crv x y]
    OKP: [crv x]
}

# Return whether a key has a kid, a known type, and every field of that type.
def valid-key [key: record]: nothing -> bool {
    let fields = $KEY_FIELDS | get --optional ($key.kty? | default '')

    $fields != null and ([kid ...$fields] | all {|field| $key | get --optional $field | is-not-empty })
}

# Download the JWKS of the identity provider, check the keys, and save the file that PostgREST reads.
def main []: nothing -> nothing {
    let jwks_file = '/etc/postgrest/jwks.json'

    log info 'JWKS retrieval started'

    let jwks = http get $env.JWKS_URI

    if not ($jwks.keys | all {|key| valid-key $key }) {
        fail 'JWKS validation failed'
    }

    $jwks | to json | save --force $jwks_file
    log info 'JWKS retrieval completed'
}
