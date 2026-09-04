#!/usr/bin/env nu

def not-empty-string [x]: any -> bool { ($x | describe) == "string" and ($x | str length) > 0 }

let jwks_file = "/etc/postgrest/jwks.json"
let jwks = http get $env.JWKS_URI

let valid = $jwks.keys
| all {|key|
        (not-empty-string $key.kid) and (
            ($key.kty == "RSA" and (not-empty-string $key.n) and (not-empty-string $key.e))
            or ($key.kty == "EC" and (not-empty-string $key.crv) and (not-empty-string $key.x) and (not-empty-string $key.y))
            or ($key.kty == "OKP" and (not-empty-string $key.crv) and (not-empty-string $key.x))
        )
    }

if not $valid {
    print --stderr "JWKS validation failed"
    exit 1
}

$jwks | to json | save -f $jwks_file
