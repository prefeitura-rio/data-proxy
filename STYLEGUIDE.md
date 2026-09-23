''# Style Guide

Conventions for working in this repository. Follow these rules before inventing a new pattern.

## Engineering principles

- Search and read the codebase before writing code. Verify conventions against source.
- Prefer deletion over another abstraction or configuration layer.
- Keep changes small and reviewable. Do not bundle unrelated work.
- Fix root causes. Do not patch symptoms.
- Use the idiomatic tool for each ecosystem.

## Python

- Model closed alternatives as tagged Pydantic unions. Match on the discriminator.
- Enumerate every case for a closed union. Use a wildcard only when the set is open.
- Keep public function names explicit. Prefix a parameter with `_` only when the calling convention requires an unused parameter.
- Use `asyncer.asyncify` for blocking calls. Do not write hand-rolled thread helpers.
- Prefer explicit parameters and named functions over dynamic `**kwargs` and opaque lambdas.
- Group related functions by domain.
- Use generic names in reusable models. Do not encode one customer or table in a generic field.
- Treat JWT claims as identity. Read authorization state from the database.
- Do not make outbound HTTP calls from request-time authorization logic.

## Nushell

Nushell scripts power Helm maintenance jobs and local development commands. Keep them small and typed.

- Declare input and output types on custom commands. Do not use `any` when a concrete type is available.
- Use full flag names in scripts, such as `open --raw` and `save --force`.
- Use `where` for filtering, `match` for dispatch, and `get --optional` for optional fields.
- Put the pipe at the start of continuation lines. Use one pipeline step per line.
- Use `$env.NAME? | default value` for optional environment variables.
- Handle external commands at the command boundary. Log failures and re-raise them when the caller cannot continue.
- Use `use std/log` and `log` for operational output. Do not use `print` for service output.
- Use `use ./lib.nu [quote-pg render-sql]` for SQL rendering.
- Use `quote-pg` for values that reach raw SQL. Do not interpolate unquoted values into `psql -c`.
- Give each script one `main` command. Use a separate script for a separate CronJob behavior.
- Run `nu --ide-check` for changed Nushell files.

## SQL safety

SQL templates live under `src/data_proxy/templates/<target>/`. Each target is one database backend. Helm maintenance templates live under `helm/files/templates/postgres/`. See [SQL Templates](docs/sql-templates.md) for metadata and naming rules.

- Use `psycopg.sql.Identifier()` and `psycopg.sql.Literal()` for Python-side SQL mappings.
- Use prepared parameters for runtime values. Never interpolate user values into SQL text.
- Quote reserved SQL identifiers. Prefer `Identifier()` in Python.
- Use `ON CONFLICT ... DO UPDATE` for idempotent upserts.
- Use `timestamptz` for timestamps and `jsonb` for queried or indexed JSON.
- Use `bigint GENERATED ALWAYS AS IDENTITY` instead of `serial` or `bigserial`.
- Keep one transaction per logical operation. Use the repository atomic transaction helper.
- Use `SECURITY DEFINER` only for deliberate privileged database functions. Grant execute to the required role only.
- Use `STABLE` for read-only functions. Mark volatile functions `VOLATILE` when explicitness helps review.
- Use `NOTIFY pgrst` after publication changes the PostgREST schema.
- Use set-based loads and deletes. Do not insert or delete rows one at a time.

### Backend rules

- PostgreSQL uses `pg_duckdb` to read Parquet from object storage. Do not download Parquet to local disk first.
- DuckDB runs in a worker thread through `asyncer.asyncify`. Do not block the event loop.
- Use one `COPY (...) TO ... (FORMAT PARQUET)` per dump task.
- Use `bigquery_scan` in DuckDB for data reads. Use the BigQuery client for metadata and partition listing.
- Restrict BigQuery scans with partition predicates.
- Use a service account or Workload Identity. Do not embed credentials in an image.

## Configuration and state

- Keep one source of truth for each fact. Do not synchronize duplicate configuration.
- Use one generic, data-driven mechanism when features solve the same problem.
- DBOS and PostgreSQL own orchestration state. Redis is cache-only. See [Architecture](docs/architecture.md).
- Keep DBOS workflows deterministic. Put external I/O and non-deterministic work in steps.
- Do not re-implement DBOS checkpointing in application code.
- See [Database Schema](docs/database.md) for roles, RLS, retention, and privileged functions.

## Testing and verification

Run the relevant canonical devenv tasks before completing a change:

```bash
devenv --profile default tasks run dp:lint dp:test
```

Use `devenv tasks` to inspect the individual checks. See [Development](docs/development.md) for local workflow details. The task group covers Python, Nushell, SQL, Helm, Docker, proxy, type, complexity, dead-code, coverage, and unit-test checks.

## Documentation

- Use Simplified Technical English. Prefer short sentences and active voice.
- Put one main idea in each sentence.
- Keep one topic in one document. Link to the owning document instead of duplicating reference material.
- Use generic examples unless a repository-specific value is required.
- Verify runtime claims against source, Helm templates, tests, or an executed command.

## Git

- Follow Conventional Commits: `type(scope)!: imperative summary`. Use lowercase and no trailing period.
- Use `feat` for added behavior, `fix` for corrected behavior, `perf` for performance changes, `refactor` for internal changes, and `docs`, `test`, `chore`, or `ci` for non-user-facing changes.
- Use `!` for a compatibility-breaking change. Explain the migration in the commit body. Do not use a `BREAKING CHANGE` footer.
- For commits that need context, add a short explanation after the subject. Add `Migration:` only when a user or operator must act:

  ```text
  refactor(sync)!: move orchestration from Redis streams to DBOS

  Redis stream state could not resume an interrupted multi-stage sync reliably.
  DBOS stores workflow checkpoints in PostgreSQL.

  Migration:
  - Remove the Redis stream configuration.
  - Deploy the DBOS worker before enabling scheduled syncs.
  ```

- Keep the subject short and user-facing. Describe behavior and impact, not only files.
- Use one logical change per commit. Squash fixup commits before merging.
- Never commit or push without explicit user approval. Stage and summarize the change, then wait.

## Planning

- Propose a plan before changes with real blast radius, such as new dependencies, cross-file renames, schema changes, or infrastructure changes.
- Pure documentation clarifications, typo fixes, and single-file wording corrections do not need a plan.
