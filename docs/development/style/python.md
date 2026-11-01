# Python

Applies to: `sync/**/*.py`.

## Code

- **PY-01** Type a password, a key or an address with a password as `SecretStr`, and call `get_secret_value()` only where the code connects. [must, review]
- **PY-02** Give a credential no default. [must, review]
- **PY-03** Use dataclasses for plain data. Use Pydantic models only for validation at boundaries. [must, review]
- **PY-04** Model closed alternatives as tagged unions, and match every case. Use `_` only for open sets. [must, review]
- **PY-05** Don't prefix internal names with `_`. Use `_` only for a required but unused parameter. [must, review]
- **PY-06** Keep `__init__.py` to one docstring line, and import from submodules. [must, ruff]
- **PY-07** Inline a one-line helper that has one caller. [should, review]
- **PY-08** Put a function in the module of its domain. Use `utils.py` only when no domain fits. [must, review]
- **PY-09** Write a multi-stage flow as a context dataclass with one method per stage, like `PlanningContext` in `planning.py`. [should, review]
- **PY-10** Use explicit parameters and named functions. Avoid `**kwargs`, opaque lambdas, `Any` and `object`. [must, ruff+pyrefly]
- **PY-11** Run blocking calls through `asyncer.asyncify`. [must, review]
- **PY-12** Treat JWT claims as identity only, and read authorization state from the database. [must, review]
- **PY-13** Don't make outbound HTTP calls in request-time authorization. [must, review]

```python
@dataclass
class PlanningContext:
    config: SyncConfig
    changed: dict[str, str] = field(default_factory=dict)

    async def detect(self) -> None: ...
    async def expand(self) -> None: ...
    def group(self) -> SyncWork: ...
```

## Data and sync

- **PY-17** Open one DuckDB connection for each concurrent task, and set its memory, threads and spill folder with the `limits` template. A connection serves one query at a time. [must, review]
- **PY-18** Write Parquet row groups of at most 10,000 rows and 32 MB (`PARQUET_ROW_GROUP_SIZE` and `PARQUET_ROW_GROUP_BYTES`), so that a dump of wide tables fits in memory. [must, review]
- **PY-19** Reconcile the serving views of every table of a planned schema, also when a run plans no change. [must, review]
- **PY-20** Parse and compute times with `whenever` (`Instant`, `PlainDateTime`). Import `datetime` only at a boundary that needs it. [should, review]

## Tooling

- **PY-14** Keep `sync:lint:py` green. It runs `ruff` with `select = ["ALL"]`, `pyrefly` with `preset = "all"`, `complexipy` with a limit of 10 for each function, and `vulture`. [must, ruff+pyrefly]
- **PY-15** Use `cast` once, in `templates.py`, where it marks the text of a repository template as `LiteralString`. [must, pyrefly]
- **PY-16** Write one long string and not an implicit string concatenation. `E501` is off. [must, ruff]

See [Logs](logs.md) for the logging rules of Python (LOG-06 to LOG-08).

<!-- nav:start -->
---

[Previous: k6 files](k6.md) | [Next: Lua layout, tests and checks](lua-layout.md) | [Index](index.md) | [Home](../../index.md)
<!-- nav:end -->
