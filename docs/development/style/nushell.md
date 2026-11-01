# Nushell

Applies to: `scripts/*.nu`, `helm/files/scripts/*.nu`, `console/notebooks/*.nu`.

Nushell scripts run the Helm jobs and the local commands.

## Code

- **NU-01** Declare input and output types. Don't use `any` when a concrete type exists. [must, review]
- **NU-02** Use full flag names, such as `open --raw` and `save --force`. [must, nu-lint]
- **NU-03** Use `where` to filter, `match` to dispatch, and `get --optional` for optional fields. [should, nu-lint]
- **NU-04** Start a continuation line with the pipe, and put one step on each line. [must, review]
- **NU-05** Read an optional variable with `$env.NAME? | default value`. [must, review]
- **NU-06** Use `default {|| $env.NAME }` for a variable that a caller may not need, because `default $env.NAME` fails when the variable isn't set. [must, review]
- **NU-07** Run SQL only through `execute-sql` from `helm/files/scripts/lib.nu`, with one template and one context record. Quote values with `quote-pg`. [must, review]
- **NU-08** Use one command for one kind of quote (`quote-identifier`, `quote-literal`). Don't pass the kind as a string. [must, review]
- **NU-09** Give each script one `main` command, and give each CronJob its own script. [must, review]
- **NU-10** Use pipelines for data transforms and `for` for ordered external side effects. [should, nu-lint]
- **NU-11** Add input and output types and `@example` blocks to an exported reusable helper. [should, review]
- **NU-12** Don't import modules across `helm/files/scripts/` and `scripts/`. They're separate codebases. [must, review]
- **NU-13** Run `nu --ide-check` on a changed file. [should, review]
- **NU-14** Write an expression that three places repeat as a helper (`app-schema`, `role-literals`). Keep one table for a list that several commands read (`IMAGES` in `scripts/lib.nu`). [should, review]

## Errors and files

- **NU-15** Stop a script with `fail $message` from `lib.nu`. It logs the message and raises an error with a label on the message in the caller. Don't call `error make`. [must, review]
- **NU-16** Read and write files only through the helpers of `lib.nu`: `read-text`, `read-data`, `write-text`, `parse-json`, `parse-toml`, `parse-yaml`, `make-dir` and `remove-path`. [must, review]
- **NU-17** Wrap `cd` and `http get` in `try` at the call, because the helpers of `lib.nu` wrap only files. [must, review]
- **NU-18** Let an error of a builtin or an external command stop the script. Use `try` only to add context that the error lacks, such as the table that failed, or to clean up. [must, review]
- **NU-19** Use `check-result` for the record that `complete` returns, and `wait-until` for a wait. Don't write the exit-code check or the polling loop again. [must, review]

```nu
✅ fail $"The table ($name) is missing"
❌ error make { msg: $"The table ($name) is missing" }
```

## Tooling and tests

- **NU-20** Keep `nu:lint:scripts` green. It runs `nu-lint` and fails on a finding, because `nu-lint` exits with 0 in that case. [must, nu-lint]
- **NU-21** Give each rule that `.nu-lint.toml` turns off a comment with the reason. [must, review]
- **NU-22** Run `devenv --profile repo tasks run nu:test:unit` after a script change. `run.nu` finds every `test ...` command, so a new test needs no registration. [must, devenv]

```nu
use ./lib.nu [quote-pg execute-sql]

for schema in $schemas {
    execute-sql postgres/create_schema.sql {schema: (quote-pg $schema identifier)}
}
```

See [Logs](logs.md) for the logging rules of Nushell (LOG-12).

<!-- nav:start -->
---

[Previous: Lua types and comments](lua-types.md) | [Next: Nix naming and code](nix.md) | [Index](index.md) | [Home](../../index.md)
<!-- nav:end -->
