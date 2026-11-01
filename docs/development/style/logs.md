# Logs

Applies to: all first-party code that logs from a deployment.

## All languages

- **LOG-01** Write lifecycle phrases that a person can read: `<Component> <action> <state>: <relevant context>`. [must, review]
- **LOG-02** Use `debug` for probe detail, `info` for normal lifecycle changes, `warning` for retries or skipped optional work, and `error` only before a terminal error. [must, review]
- **LOG-03** Never log credentials, tokens, connection strings, request bodies, cache values, SQL values, or query strings. [must, review]
- **LOG-04** Don't use `print` for production logs. A documented machine-output protocol may write to stdout when callers parse it. [must, ruff]
- **LOG-05** Keep the output of external tools as it is. Don't parse or reformat the output of `rclone`, `psql`, `pg_dump`, `duckdb` or `kubectl`. [must, review]

```text
S3 bucket is not ready; retrying: bucket=test-bucket error=connection refused
```

## Python

- **LOG-06** Use the standard-library logger from `sync.log`. Don't configure handlers in application modules or create module-specific loggers. [must, review]
- **LOG-07** Set the log level only through `Settings.LOG_LEVEL`. Don't set a level in application modules. [must, review]
- **LOG-08** Keep the DBOS workflow, schema and table context through the context values in `sync.log`. [must, review]

```python
logger.info("DuckLake commit completed: published_tables=%d", len(published_tables))
```

## Lua

- **LOG-09** Write logs only through `log.write`. Don't call `ngx.log` in other modules. [must, review]
- **LOG-10** Use the fixed messages in `constants.log.messages`, and add the context as `name=value` pairs. [must, review]
- **LOG-11** Use `log.note` for an event that has no request, such as a timer or the Redis store. [must, review]

## Nushell and k6

- **LOG-12** In Nushell, use `std/log` directly. A command in `scripts/` may use `print` for machine output, such as a CI output line. It may also use `print` for a log stream that it shows to a person. [must, review]
- **LOG-13** In k6, write log lines with `info` and `warn` from `k6/lib/log.js`, with an event name and `key=value` fields. Never use `console`. [must, review]

<!-- nav:start -->
---

[Previous: Principles](principles.md) | [Next: Tests](tests.md) | [Index](index.md) | [Home](../../index.md)
<!-- nav:end -->
