# Style Guide

Conventions for working in this repository. Follow these before inventing new patterns.

## Engineering Principles

- Search and read the codebase before writing anything. Do not assume a convention; verify it against source.
- Deletion beats addition. Prefer removing code, fields, or config over adding a new layer around a problem.
- Baby steps. Keep diffs small and reviewable; do not bundle unrelated changes.
- Never patch symptoms. Find and fix the root cause, even if it takes longer.
- Prefer idiomatic tooling for each ecosystem (Python, Helm, SQL) over custom scripting.

## Python

- Discriminated unions over `isinstance`/`hasattr` duck typing. Model alternatives as tagged Pydantic models (`Literal` discriminator field) and `match`/`case` on the tag.
- Exhaustive `match`/`case`: enumerate every case explicitly. Do not fall back to a wildcard `case _` when the case set is closed and known.
- No leading-underscore function names as a style choice. Only prefix a parameter with `_` when it is genuinely unused by the calling convention and never passed by keyword anywhere in the codebase. If a parameter is called by keyword at any real call site (e.g. `job_config=`, `approximate=`), keep its name and, if a linter flags it as unused, suppress via `ignore_names` instead of renaming.
- Prefer `asyncer.asyncify` to wrap blocking calls over hand-rolled thread/executor code.
- Avoid dynamic `**kwargs` passthroughs and bare `lambda`s where a named function or an explicit parameter list documents intent better.
- Group related functions by module/domain, not by kind (don't split a single feature's helpers across unrelated files).
- Within a file scoped entirely to one domain, prefer a short, unqualified name (`table`) over a prefixed one (`bq_table`) -- but check every function in the file for name collisions first.
- Avoid domain-specific field/column names in generic, reusable schemas (e.g. an access-control table should use `unit_type`/`unit_id`, not a hardcoded customer's terminology like `cras`).
- A JWT carries identity, not authorization state. Never encode "what this user can access" as a token claim; look it up from data at request time.
- No outbound HTTP calls from request-time authorization logic (e.g. RLS checks). Authorization must resolve from data already in the database.
- Generalize hardcoded identifiers (customer names, specific org/table names) into configuration the first time you touch code that has them.
- Reserved SQL keywords used as identifiers (e.g. a role literally named `user`) must be double-quoted in raw SQL or shell heredocs. `psycopg.sql.Identifier()` already handles this automatically -- prefer it over hand-quoting.
## Nushell

Nushell powers the Helm CronJob scripts (cleanup, retention, migration) and `lib.nu`. Keep these scripts small and typed.

- Use `def name [args]: input_type -> output_type` signatures. Declare every parameter type. Do not rely on `any`.
- Spell flags out in full: `save --force`, `open --raw`, `uniq --count`. Short flags are for the prompt, not code.
- Use `where` for filtering, `match` for dispatch, `get --optional` for field extraction. Do not use `filter` or long `if/else if` chains.
- Place `|` at the start of continuation lines, one step per line. Group stages that read as one action (`| lines | str trim`).
- Use `$"($var)"` for interpolation. Never use bash `$VAR` or `$(cmd)`. Use `$env.VAR`.
- Use `try { ... } catch { |err| log error $'... ($err.msg)' }` around every external command (`psql`, `minijinja-cli`, `redis-cli`). Log and continue or re-raise with `error make`.
- Use `use std/log` for logging. Use `log info`, `log error`. Do not use `print` for operational output.
- Use `use ./lib.nu [quote-pg render-sql]` for SQL rendering. Never hand-build SQL with string interpolation. Pass a `record` context to `render-sql`; let Jinja do the presentation.
- Use `quote-pg $value identifier` and `quote-pg $value literal` for any value that reaches raw SQL outside a Jinja template. Never inline a variable into a `psql -c` string.
- Read configuration from `$env.VAR? | default ...`. Do not assume an env var is set.
- Use `--tuples-only` on the `postgres` helper when you parse the output as data. Omit it for DDL that produces no result.
- One `main` per script. Prefer script mode (`nu script.nu subcommand`) over module mode. Do not share a script across CronJobs with a flag; make a second script.
- Run `nu --ide-check` to validate syntax before committing. Use the `nushell` skill for non-trivial pipelines.

## SQL

SQL lives in Jinja templates under `src/dp/sql/<target>/`. Each target is one database backend. Every template starts with a `{# kind/description/inputs #}` header (see Jinja SQL Templates above).

### PostgreSQL (application and DBOS system database)

- Use `psycopg.sql.Identifier()` and `psycopg.sql.Literal()` in Python mappings for identifiers and literals. Let Jinja render only presentation loops and conditionals.
- Use prepared parameters (`%(name)s` or `%s`) for all runtime values. Never interpolate a user value into SQL text.
- Use `ON CONFLICT ... DO UPDATE` for idempotent upserts. Prefer it over `DELETE` + `INSERT`.
- Use `timestamptz` for timestamps. Do not store timestamps as `text` or `timestamp`.
- Use `jsonb`, not `json`, for stored JSON that is queried or indexed.
- Use `text` with a `CHECK` constraint over `varchar(n)`. Use `bigint GENERATED ALWAYS AS IDENTITY` over `serial`/`bigserial`.
- Quote reserved keywords used as identifiers (e.g. `"table"`, `"user"`) in raw SQL. `Identifier()` handles this in Python.
- Keep one transaction per logical operation. Use the `atomic` context manager for commit-on-success, rollback-on-error.
- Use `SECURITY DEFINER` functions for RLS-bypassing access (e.g. the BigQuery fallback function). Grant execute to the authenticator role only.
- Use `STABLE` for functions that only read. Mark volatile functions `VOLATILE` explicitly.
- Use `NOTIFY pgrst` to reload PostgREST schema cache after a publication. Use `LISTEN`/`NOTIFY` for cross-process signals. Do not poll a table in a tight loop.
- Use `DELETE FROM ... WHERE {{ column }} < now() - interval '{{ retention }}'` for time-window retention. Run it from a K8s CronJob. Do not use `pg_partman`.
- Use `CREATE TABLE ... AS SELECT` or `INSERT ... SELECT` for bulk loads from Parquet via `pg_duckdb`. Do not row-by-row insert.

### DuckDB (extraction and fallback)

- Use `COPY (...) TO path (FORMAT PARQUET)` for extraction. One `COPY` per dump task.
- Use `bigquery_scan('project.dataset.table')` for BigQuery reads. Pass the table reference as a literal.
- Use `LOAD bigquery` and `LOAD httpfs` once per connection. Set the S3 secret once at connection setup.
- Use `read_parquet('s3://...')` for S3 reads inside Postgres via `pg_duckdb`. Do not download Parquet to disk first.
- Use `to_json(column)` for STRUCT/RECORD columns during extraction. Cast to `jsonb` on the Postgres side during load.
- Use DuckDB in a worker thread via `asyncer.asyncify`. Do not call blocking DuckDB APIs on the event loop.
- Use a temporary directory for multi-partition merges. Clean it up in a `finally` block.

### BigQuery (source queries)

- Use the BigQuery client for metadata only (`get_table`, partition listing). Do not run data queries through the client.
- Use `bigquery_scan` in DuckDB for actual data reads. The client is for planning, not extraction.
- Use partition predicates (`_PARTITIONTIME`, `__NULL__`) to limit scanned bytes. The producer already filters to the last N partitions. Do not re-filter downstream.
- Use a service account or Workload Identity. Do not embed a key file in the image.

## Configuration & Schema Design

- One universal mechanism beats several special-cased ones. If two features solve the same problem for different customers/tables, unify them into one generic, data-driven mechanism instead of keeping both.
- A single, explicit source of truth for each fact. If two config locations can express the same thing (e.g. "which schemas exist"), delete one -- do not synchronize two by hand.
- Static Helm `.Files.Get` SQL files cannot be parameterized. If a Helm value needs to reach the SQL, convert the file to a `{{ define }}` template.

## Concurrency & State (sync pipeline specifics)

- DBOS/Postgres is the source of truth for pipeline state. Redis is cache-only.
- DBOS workflows and steps checkpoint results. Do not re-implement checkpointing in application code.
- Every background loop has exactly one termination trigger. Do not add a second, redundant shutdown path.
- Prefer `logger.exception` inside `except` blocks over `logger.error` plus manual traceback formatting.
- Sequential orchestration with shared state uses a dataclass context with step methods — no `Pipeline` suffix, no `Protocol`, no `run()` wrapper. The caller creates the context and calls steps in order.

## Jinja SQL Templates

Use Jinja for SQL template presentation logic. Use one name for each semantic role.

**Core names**: Use `schema`, `table`, `view`, `function`, `policy`, and `index` for PostgreSQL objects. Do not use `name` when the object type is known.

**Column names**: Use `column` for one column. Use `columns` for a collection of columns. Do not use `cols` or `select_cols` as aliases.

**Paths**: Use `path` when a template has one path. Use `source` and `target` when a template has two distinct endpoints. Use `scratch_path` when the path is specifically temporary storage.

**Roles**: Use `user_role`, `anonymous_role`, `authenticator_role`, and `policy_writer_role`. Do not shorten these names.

**Predicates**: Use `scope` for a schema-scope predicate. Use `predicate` for a row or partition predicate. Use `claim_setting` for a PostgreSQL session-setting name.

**Template context**: Keep SQLFluff context values close to the template family that uses them. Do not create one global context bag with unrelated values. A context value must represent a valid example for the template.

**Runtime mappings**: Use the same names in Python mappings and Jinja variables. Keep PostgreSQL identifier, literal, type, and prepared-parameter semantics in Python helpers. Move only presentation loops and conditional formatting into Jinja.

**Example**:

```python
render_template(
    "postgres/create_bq_view",
    {
        "schema": Identifier(schema),
        "view": Identifier(view_name),
        "function": Identifier(function_name),
        "columns": column_expressions,
    },
)
```

```sql
CREATE OR REPLACE VIEW {{ schema }}.{{ view }} AS
SELECT {% for column in columns %}{{ column }}{% if not loop.last %}, {% endif %}{% endfor %}
FROM {{ schema }}.{{ function }}();
```

Do not merge variables that have different meanings. `table`, `view`, and `function` are all identifiers. They are not interchangeable. `path`, `source`, and `target` are all strings. They are not interchangeable when a template uses more than one.

### Template documentation

Document each template once at the top of its source file. Use one multiline JSON object inside the native template comment.

```jinja
{#
{
  "kind": "template",
  "description": "Create a table from Parquet.",
  "inputs": {
    "schema": "Destination PostgreSQL schema.",
    "table": "Destination PostgreSQL table.",
    "path": "Parquet file path."
  }
}
#}
```

Use `kind: template` with `description`. Add `inputs` only when the template has inputs. Keep input descriptions short.

Document every macro or Helm `define` once. Use one JSON header immediately before the definition.

```gotemplate
{{/*
{
  "kind": "macro",
  "name": "data-proxy.name",
  "description": "Return the chart name truncated to the Kubernetes limit.",
  "inputs": {
    "context": "Helm chart context."
  },
  "returns": "A Kubernetes-safe chart name."
}
*/}}
{{- define "data-proxy.name" -}}
...
{{- end -}}
```

Use `macros.*` for Jinja and minijinja macro files. Keep Helm helpers in `_helpers.tpl`, which is Helm's standard helper file. Use one JSON header per macro or `define`.

Put one blank line before the header. Put no blank line between the header and definition. Put one blank line after the definition. Add `name`, `description`, and `returns`. Add `inputs` when the macro accepts inputs. Do not generate metadata at runtime. Do not document ordinary called functions with macro metadata.

Use `{# ... #}` for Jinja and minijinja. Use `{{/* ... */}}` for Go templates. The metadata must stay inside the source comment and must not appear in rendered output.

## Testing & Verification

Before considering any change complete, run and confirm clean output from:

```bash
uv run pytest --cov=dp --cov-report=term-missing   # 100% coverage, all green
uv run ruff check
uv run ruff format --check
uv run basedpyright src/ tests/
uv run complexipy src/ tests/
uv run vulture src/ tests/
uv run sqlfluff lint src/dp/sql/
helm lint helm/ -f helm/ci/test-values.yaml
helm lint helm/ -f helm/ci/test-values-ha.yaml
helm unittest helm/
```

- Docstrings are brief: one line stating what the function does, not how.
- When a doc or comment claims something about runtime behavior (scaling, retries, diagrams), verify it against the actual source/Helm templates before writing it down. Do not describe behavior from memory or assumption.
- When explaining _why_ a technical choice was made, ground the explanation in verified facts (changelogs, source, issue trackers), not assumption.

## Documentation

- Simplified Technical English (ASD-STE100): short sentences, one idea per sentence, consistent terminology, active voice.
- One clause per sentence. Split any sentence joined by "and", "but", "so", "which", or a colon into two sentences.
- Avoid vague qualifiers and hedges: "too", "plus", "back to", "either way", "instead of", "coarse", "regardless". State the fact plainly instead.
- Each doc file answers exactly one question. Do not split one topic across two files, and do not create a stub file whose scope overlaps another file's.
- Use generic, domain-neutral examples (e.g. "school", "student") instead of repo-specific customer names when illustrating a concept.
- For conceptual/onboarding explanations, prefer a narrative walkthrough (a hypothetical user, a numbered sequence of steps) over a bare command checklist. Save checklists for purely mechanical how-tos.
- Keep list formatting consistent within a document: `**Bold term**: description.` (colon, not period, after the bold term).
- Verify registry paths, image names, and org/repo references against actual CI workflow config or `git remote -v` -- never assume them.
- Prefer capturing a real, executed example (command + actual output) over a fabricated one when documenting an API or CLI.

## Git

- Follow Conventional Commits, matching this repository's history: `type: imperative summary` (`feat`, `fix`, `refactor`, `docs`, `chore`, `test`), lowercase, no trailing period.
- Never commit or push without explicit user approval. Stage and summarize the change, then wait.
- Keep commits scoped to one logical change matching the diff, not the whole working session.

## Planning

- For any change with real blast radius (new dependency, cross-file rename, schema/model restructuring, infra template changes), propose a plan and get approval before implementing.
- Pure documentation clarifications, typo fixes, and single-file wording corrections do not need a plan -- just make them.