use std/assert
use ../lib.nu [postgres-ready-args quote-pg schema-list sync-config wait-for-postgres]

# Verify that schema-list passes all configured schemas through without a filter.
export def "test schema-list-returns-all-schemas" []: nothing -> nothing {
    with-env {
        SCHEMA: ''
    } {
        let config = {
            schemas: {
                alpha: {}
                beta: {}
            }
        }
        assert equal ($config | schema-list) [alpha beta]
    }
}

# Verify that schema-list honors the SCHEMA environment filter.
export def "test schema-list-filters-schema" []: nothing -> nothing {
    with-env {
        SCHEMA: beta
    } {
        let config = {
            schemas: {
                alpha: {}
                beta: {}
            }
        }
        assert equal ($config | schema-list) [beta]
    }
}

# Verify that quote-pg escapes PostgreSQL identifiers and literals.
export def "test quote-pg-escapes-identifier-and-literal" []: nothing -> nothing {
    assert equal (quote-pg 'a"b' identifier) '"a""b"'
    assert equal (quote-pg "a'b" literal) "'a''b'"
}

# Verify that sync-config reports an unavailable configuration file.
export def "test sync-config-fails-for-missing-file" []: nothing -> nothing {
    with-env {
        SYNC_CONFIG_PATH: /tmp/helm-files-tests-missing-sync.json
    } {
        let failed = try {
            sync-config
            false
        } catch {
            true
        }
        assert $failed
    }
}

# Verify that readiness arguments include an optional connection DSN.
export def "test postgres-ready-args-include-dsn" []: nothing -> nothing {
    assert equal (postgres-ready-args) [
        --no-psqlrc
        --quiet
        -t
        -A
        -c
        'SELECT 1'
    ]
    assert equal (postgres-ready-args postgres://test) [
        postgres://test
        --no-psqlrc
        --quiet
        -t
        -A
        -c
        'SELECT 1'
    ]
}

# Verify that wait-for-postgres passes constructed arguments into a mock probe.
export def "test wait-for-postgres-passes-args-to-probe" []: nothing -> nothing {
    wait-for-postgres postgres://test --timeout 0sec --probe {|args|
        assert equal $args [postgres://test --no-psqlrc --quiet -t -A -c 'SELECT 1']
        {
            exit_code: 0
            stderr: ''
        }
    }
}
