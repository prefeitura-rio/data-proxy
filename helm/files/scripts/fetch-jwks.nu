use std/log
use ./lib.nu [fail write-text]

# The fields that a key of each type needs, next to its kid.
const KEY_FIELDS = {
    RSA: [n e]
    EC: [crv x y]
    OKP: [crv x]
}

# Return whether a key has a kid, a known type, and every field of that type.
def valid-key [key: record]: nothing -> bool {
    let fields = if $key.kty? == null { null } else { $KEY_FIELDS | get --optional $key.kty }

    $fields != null and ([kid ...$fields] | all {|field| ($key | get --optional $field) not-in [null ''] })
}

# Download the JWKS of the identity provider, check the keys, and save the file that PostgREST reads.
def main []: nothing -> nothing {
    let jwks_file = '/etc/postgrest/jwks.json'

    log info 'JWKS retrieval started'

    let jwks = try {
        http get $env.JWKS_URI
    } catch {|err|
        fail $'Cannot download the JWKS from ($env.JWKS_URI): ($err.msg)'
    }

    if not ($jwks.keys | all {|key| valid-key $key }) {
        fail 'JWKS validation failed'
    }

    $jwks | to json | write-text $jwks_file
    log info 'JWKS retrieval completed'
}
