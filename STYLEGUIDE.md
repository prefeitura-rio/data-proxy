# Style Guide

Rules for this repository. Follow an existing pattern before you add a new one.

## Principles

- Read the code before you write code.
- Prefer deletion to a new abstraction or configuration layer.
- Make small, reviewable changes. Don't bundle unrelated work.
- Fix the root cause, not the symptom.
- Keep one source of truth for each fact.
- Use the idiomatic tool for each ecosystem.

## Python

- Use dataclasses for plain data. Use Pydantic models only for validation at boundaries.
- Model closed alternatives as tagged unions. Match every case; use `_` only for open sets.
- Don't prefix internal names with `_`. Use `_` only for a required but unused parameter.
- Keep `__init__.py` empty. Import from submodules.
- Inline a one-line helper that has one caller.
- Put a function in the module of its domain. Use `utils.py` only when no domain fits.
- Write multi-stage flows as a context dataclass with one method per stage, like `PlanningContext` in `planning.py`.
- Use explicit parameters and named functions. Avoid `**kwargs`, opaque lambdas, `Any`, and `object`.
- Run blocking calls through `asyncer.asyncify`.
- Treat JWT claims as identity only. Read authorization state from the database.
- Don't make outbound HTTP calls in request-time authorization.

```python
@dataclass
class PlanningContext:
    config: SyncConfig
    changed: dict[str, str] = field(default_factory=dict)

    async def detect(self) -> None: ...
    async def expand(self) -> None: ...
    def group(self) -> SyncWork: ...
```

## Nushell

Nushell scripts run the Helm jobs and the local commands.

- Declare input and output types. Don't use `any` when a concrete type exists.
- Use full flag names: `open --raw`, `save --force`.
- Use `where` to filter, `match` to dispatch, and `get --optional` for optional fields.
- Start continuation lines with the pipe. Put one step on each line.
- Read optional variables with `$env.NAME? | default value`.
- Log with `use std/log`. Don't use `print` for service output.
- Run SQL only through `execute-sql` from `helm/files/lib.nu`: one template, one context record. Quote values with `quote-pg`.
- Give each script one `main` command, and each CronJob its own script.
- Use pipelines for data transforms and `for` for ordered external side effects.
- Add input/output types and `@example` blocks to exported reusable helpers.
- `helm/files/` and `scripts/` are separate codebases. Do not import modules across them.
- Run `nu --ide-check` on changed files.

Run the Helm Nushell suite after Helm script changes:

```bash
nu helm/files/tests/run.nu
```

```nu
use ./lib.nu [quote-pg execute-sql]

for schema in $schemas {
    execute-sql postgres/create_schema.sql {schema: (quote-pg $schema identifier)}
}
```

## SQL

Templates live in `src/data_proxy/templates/<backend>/`. Helm job templates live in `helm/files/templates/postgres/`. See [SQL Templates](docs/sql-templates.md).

- Build SQL mappings with `Identifier()` and `Literal()`. Use parameters for runtime values.
- Never put user values into SQL text.
- Use `ON CONFLICT ... DO UPDATE` for upserts.
- Use `timestamptz`, `jsonb`, and `bigint GENERATED ALWAYS AS IDENTITY`.
- Use one transaction per logical operation, with the `atomic()` helper.
- Use `SECURITY DEFINER` only on purpose. Grant `EXECUTE` to the required role only.
- Mark read-only functions `STABLE`.
- Send `NOTIFY pgrst` after a PostgREST schema change.
- Load and delete in sets, not row by row.

```python
mapping = {"table": Identifier(schema, name), "limit": Literal(n)}
```

### Backends

- PostgreSQL reads Parquet from object storage through `pg_duckdb`. Don't download it first.
- DuckDB writes one `COPY (...) TO ... (FORMAT PARQUET)` for each dump task.
- Read BigQuery data with `bigquery_scan` and partition predicates. Read metadata with the BigQuery client.
- Use a service account or Workload Identity. Don't put credentials in an image.

## State

- DBOS and PostgreSQL own orchestration state. Redis is a cache only. See [Architecture](docs/architecture.md).
- Keep DBOS workflows deterministic. Put I/O in steps.
- Don't re-implement DBOS checkpoints.
- See [Database Schema](docs/database.md) for roles, RLS, and privileged functions.

## Tests

Run the checks before you finish a change:

```bash
devenv --profile default tasks run dp:lint dp:test
```

Put each test at the lowest level that can prove the behavior. Don't repeat a check at a higher level.

| Level       | Location                                            | Runs against                                                   | Covers                                                                  |
| ----------- | --------------------------------------------------- | -------------------------------------------------------------- | ----------------------------------------------------------------------- |
| Unit        | `tests/unit/`, `proxy/proxy.test.ts`, `helm/tests/` | One process: mocks, local files, in-memory DuckDB              | Pure logic, DuckDB templates, workflow order, proxy, Helm manifests     |
| Integration | `tests/integration/`                                | Testcontainers: PostgreSQL with pg_duckdb, Silo (S3), Valkey   | PostgreSQL and Helm job SQL, DuckLake reads, routing, RLS, state, cache |
| E2E         | `k6/e2e.ts`, `k6/perf.ts`                           | The deployed minikube cluster (`nu scripts/cluster.nu k6 e2e`) | Sync, Keycloak and Istio auth, proxy, PostgREST headers, split sources  |

- Unit tests don't start containers or open network connections.
- Integration tests don't mock PostgreSQL. Mock only external services, such as BigQuery.
- E2E tests cover only what needs the deployed stack.
- Test modules hold only tests. Fixtures go in `tests/fixtures/`, test-double dataclasses in `tests/fixtures/types.py`, and builders in `tests/helpers.py`.
- Put test SQL in `tests/templates/<backend>/`, with the same headers and names as `src/` templates. Inline only one short statement, such as `SELECT set_config(...)` or `SET ROLE`.
- Merge small test files that cover one module.
- Use table-driven cases with an `id` that names the scenario. Use Hypothesis only for real properties.
- Assert exact values. Don't use substring checks.
- Don't assert the full contents of a registry that grows.
- Don't test field storage, defaults, or "no exception was raised".

```python
@pytest.mark.parametrize(
    ("selection", "expected"),
    [
        pytest.param(RangeSelection(partition_id="10", column="id", lower=10, upper=20),
                     '("id" >= 10 AND "id" < 20)', id="range"),
        pytest.param(RemainderSelection(column="id", start=0, end=100),
                     '("id" IS NULL OR "id" < 0 OR "id" >= 100)', id="remainder"),
    ],
)
def test_renders_exact_predicate(selection, expected):
    assert render(partition_condition(partition_for(selection))) == expected
```

## Documentation

- Use Simplified Technical English: short sentences, active voice, one idea per sentence.
- Keep one topic in one document. Link to the owner; don't copy.
- Use generic examples unless a real value is required.
- Verify claims against source, Helm templates, tests, or a command you ran.

## Git

- Use Conventional Commits: `type(scope)!: imperative summary`, lowercase, no trailing period.
- Types: `feat` for new user-visible capability; `fix` for a user-visible defect; `refactor` for internal restructuring; `test` for test-only work; `docs` for documentation-only work; `chore` for CI, tooling, dependencies, and maintenance.
- Do not use `perf` or `ci`: classify release-worthy performance work as `feat` or `fix`, and classify CI work as `chore`.
- Release levels: `feat!:` and `fix!:` bump major; `feat:` bumps minor; `fix:` bumps patch; all other types create no release.
- Use `!` only with `feat` or `fix` release commits. Explain the migration in the body. Don't use a `BREAKING CHANGE` footer.
- Make one logical change per commit. Squash fixups before merge.
- Never commit or push without explicit approval. Stage, summarize, and wait.

```text
feat(sync)!: require DBOS workflow recovery

Sync recovery now requires the DBOS worker and workflow state in PostgreSQL.

Migration:
- Deploy the DBOS worker before you enable scheduled syncs.
- Verify the DBOS system database is reachable.
```

## Planning

- Propose a plan before a change with a real blast radius: new dependencies, cross-file renames, schema changes, or infrastructure changes.
- Wording fixes and small documentation changes don't need a plan.
