use std/log
use ./lib.nu [fail jwks-key-valid ]

# Download the JWKS of the identity provider, check the keys, and save the file that PostgREST reads.
def main []: nothing -> nothing {
    let jwks_file = '/etc/postgrest/jwks.json'

    log info 'JWKS retrieval started'

    let jwks = try {
        http get $env.JWKS_URI
    } catch {|err|
        fail $'Cannot download the JWKS from ($env.JWKS_URI): ($err.msg)'
    }

    if not ($jwks.keys | all {|key| $key | jwks-key-valid }) {
        fail 'JWKS validation failed'
    }

    $jwks | to json | save --force $jwks_file
    log info 'JWKS retrieval completed'
}
