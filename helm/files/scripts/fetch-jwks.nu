use std/log
use ./lib.nu [fail]

# Return whether a string is non-empty.
def is-non-empty-string [value: string]: nothing -> bool {
    ($value | describe) == string and ($value | str length) > 0
}

def main []: nothing -> nothing {
    let jwks_file = '/etc/postgrest/jwks.json'

    log info 'JWKS retrieval started'

    try {
        let jwks = http get $env.JWKS_URI
        let valid = $jwks.keys
        | all {|key|
            (is-non-empty-string $key.kid) and (
                ($key.kty == RSA and (is-non-empty-string $key.n) and (is-non-empty-string $key.e))
                or ($key.kty == EC and (is-non-empty-string $key.crv) and (is-non-empty-string $key.x) and (is-non-empty-string $key.y))
                or ($key.kty == OKP and (is-non-empty-string $key.crv) and (is-non-empty-string $key.x))
            )
        }

        if not $valid {
            fail 'JWKS validation failed' {
                command: main
                span: (metadata $env.JWKS_URI).span
            }
        }

        $jwks | to json | save --force $jwks_file
        log info 'JWKS retrieval completed'
    } catch {|err| fail $'JWKS fetch failed: ($err.msg)' {
            command: main
            span: (metadata $env.JWKS_URI).span
        } }
}
