use std/assert
use ../lib.nu [
    app-schema
    check-result
    data-prefix
    orphans
    postgres-ready-args
    quote-identifier
    quote-literal
    rclone-env
    rbac-context
    role-literals
    schema-list
    sync-config
    wait-for-postgres
    wait-until
]

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

# Verify that quote-identifier doubles the double quotes of a name.
export def "test quote-identifier-escapes-double-quotes" []: nothing -> nothing {
    assert equal (quote-identifier 'a"b') '"a""b"'
}

# Verify that quote-literal doubles the single quotes of a value.
export def "test quote-literal-escapes-single-quotes" []: nothing -> nothing {
    assert equal (quote-literal "a'b") "'a''b'"
}

# Verify that app-schema reads DBOS_APP_SCHEMA and falls back to data_proxy.
export def "test app-schema-reads-the-setting-or-the-default" []: nothing -> nothing {
    with-env {DBOS_APP_SCHEMA: 'sales"x'} {
        assert equal (app-schema) '"sales""x"'
    }

    hide-env --ignore-errors DBOS_APP_SCHEMA
    assert equal (app-schema) '"data_proxy"'
}

# Verify that role-literals quotes each name and separates them with commas.
export def "test role-literals-quotes-each-name" []: nothing -> nothing {
    assert equal ([dp_api "o'brien"] | role-literals) "'dp_api', 'o''brien'"
    assert equal ([] | role-literals) ''
}

# Verify that check-result returns the trimmed output on success and fails with the error text otherwise.
export def "test check-result-returns-output-or-fails" []: nothing -> nothing {
    assert equal ({stdout: " ok\n", stderr: '', exit_code: 0} | check-result 'the command') 'ok'

    let failed = try {
        {stdout: '', stderr: 'boom', exit_code: 1} | check-result 'the command'
        ''
    } catch {|err| $err.msg }

    assert equal $failed 'the command failed: boom'
}

# Verify that wait-until returns when the check is true and fails when the timeout ends first.
export def "test wait-until-returns-or-times-out" []: nothing -> nothing {
    wait-until 'a true check' {|| true } --timeout 1sec

    let failed = try {
        wait-until 'a false check' {|| false } --timeout 0sec --interval 1ms
        ''
    } catch {|err| $err.msg }

    assert ($failed | str starts-with 'Timed out after')
}

# Verify that sync-config reports a database that it cannot read.
export def "test sync-config-fails-without-a-database" []: nothing -> nothing {
    with-env {
        PG_DATABASE_URL: 'postgresql://nobody@127.0.0.1:1/missing?connect_timeout=1'
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

# Verify that rbac-context parses the rules file and hashes the exact text of the file.
export def "test rbac-context-reads-the-rules-and-hashes-the-text" []: nothing -> nothing {
    let file = mktemp --tmpdir
    "abc = 1\n" | save --force $file

    let context = rbac-context $file

    assert equal $context.rules {abc: 1}
    assert equal $context.source_hash 78f1a1656aa4c78dbb60ac7b5d1883ac87bd79028f8e85561ba00cb0f339cf89
    rm $file
}

# Verify that rbac-context fails with a clear message when the rules file is missing.
export def "test rbac-context-fails-without-the-file" []: nothing -> nothing {
    let failed = try {
        rbac-context /nonexistent/rbac.toml
        ''
    } catch {|err| $err.msg }

    assert equal $failed 'The rules file does not exist: /nonexistent/rbac.toml'
}

# Verify that a schema with both marker tables and no row in the console is an orphan, and nothing else is.
export def "test orphans-need-both-marker-tables-and-no-console-row" []: nothing -> nothing {
    let relations = [
        {schema: test, table: access_policy}
        {schema: test, table: access_log}
        {schema: e2e_new, table: access_policy}
        {schema: e2e_new, table: access_log}
        {schema: half, table: access_policy}
        {schema: other, table: people}
    ]

    assert equal ($relations | orphans [test]) [e2e_new]
    assert equal ($relations | orphans [test e2e_new]) []
}

# Verify that the schemas of the system are never orphans, even with both marker tables.
export def "test orphans-never-include-system-schemas" []: nothing -> nothing {
    let relations = [console data_proxy dbos rbac public]
    | each {|schema|
        [
            {schema: $schema, table: access_policy}
            {schema: $schema, table: access_log}
        ]
    }
    | flatten

    assert equal ($relations | orphans []) []
}

# Verify that the data prefix of a schema is the folder of the catalog path.
export def "test data-prefix-names-the-schema-folder" []: nothing -> nothing {
    assert equal (data-prefix bucket ducklake e2e_new) 'store:bucket/ducklake/e2e_new'
}

# Verify that a name that could leave its folder gets no prefix, so that nothing outside it is purged.
export def "test data-prefix-refuses-a-name-that-leaves-the-folder" []: nothing -> nothing {
    for schema in ['' 'a/b' '..' 'a/../b'] {
        let failed = try {
            data-prefix bucket ducklake $schema
            ''
        } catch {|err| $err.msg }

        assert equal $failed $'Refusing to purge the data of the schema "($schema)"'
    }
}

# Verify that rclone-env needs no AWS variable when the caller passes both keys, as the bucket job does.
export def "test rclone-env-uses-the-given-keys-without-aws-variables" []: nothing -> nothing {
    hide-env --ignore-errors AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY

    with-env {S3_ENDPOINT: 'seaweed:8333'} {
        let remote = rclone-env --access-key key --secret-key secret

        assert equal $remote.RCLONE_CONFIG_STORE_ACCESS_KEY_ID key
        assert equal $remote.RCLONE_CONFIG_STORE_SECRET_ACCESS_KEY secret
    }
}
