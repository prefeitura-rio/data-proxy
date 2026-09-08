#!/usr/bin/env nu

use std/log

# Check whether a string is non-empty
def not-empty-string [x: string]: nothing -> bool { ($x | describe) == string and ($x | str length) > 0 }

let jwks_file = '/etc/postgrest/jwks.json'

log info $'Fetching JWKS from ($env.JWKS_URI)'

try {
    let jwks = http get $env.JWKS_URI

    let valid = $jwks.keys
    | all {|key|
            (not-empty-string $key.kid) and (
                ($key.kty == RSA and (not-empty-string $key.n) and (not-empty-string $key.e))
                or ($key.kty == EC and (not-empty-string $key.crv) and (not-empty-string $key.x) and (not-empty-string $key.y))
                or ($key.kty == OKP and (not-empty-string $key.crv) and (not-empty-string $key.x))
            )
        }

    if not $valid {
        log error 'JWKS validation failed'
        exit 1
    }

    $jwks | to json | save --force $jwks_file
    log info 'JWKS fetched and validated'
} catch {|err|
    log error $'JWKS fetch failed: ($err.msg)'
    exit 1
}
