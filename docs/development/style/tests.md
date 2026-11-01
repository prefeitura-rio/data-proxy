# Tests

Applies to: every test, in `sync/`, `proxy/`, `console/`, `helm/` and `k6/`.

## Levels and tasks

- **TEST-01** Put each test at the lowest level that proves the behavior. Don't repeat a check at a higher level. [must, review]
- **TEST-02** Make a unit test check one unit exhaustively: a function, a class, a method or an SQL template. Cover every input, every branch and the edge cases. [must, review]
- **TEST-03** Make an integration test check one behavior that can cross units or services. Examples are a state commit, a catalog read and a request through the proxy. [must, review]
- **TEST-04** Don't let the infrastructure decide the level. A template test on the real PostgreSQL server is a unit test, because it checks that template alone. [must, review]
- **TEST-05** Name the task type by what the test needs. A `unit` task needs no service, and an `int` task starts services. [must, devenv]
- **TEST-06** Mark a test that needs the PostgreSQL server with `postgres`. `sync:test:unit` skips it, and `sync:test:int` runs it. [must, devenv]
- **TEST-07** Call no service in a unit test, except the engine of the template that it tests: the PostgreSQL test server or an in-memory DuckDB. [must, review]
- **TEST-08** Don't mock PostgreSQL in an integration test. Mock only external services, such as BigQuery. [must, review]
- **TEST-09** Use E2E tests only for what needs the deployed stack. [should, review]

| Level | Location | Task | Covers |
| --- | --- | --- | --- |
| Unit | `sync/tests/unit/`, `proxy/tests/unit/`, `helm/tests/` | `sync:test:unit`, `proxy:test:unit`, `chart:test:unit` | One behavior of a function, class, method or template |
| Integration | `sync/tests/integration/` | `sync:test:int` | Flows that cross the database, the cache or the storage |
| Integration | `proxy/tests/integration/` | `proxy:test:int` | The request path: routing, retry, cache, filter pushdown |
| Unit | `console/ui/src/**/*.test.ts` | `console:test:unit` | One module of `lib/` and `lib/server/` |
| Integration | `console/ui/tests/` | `console:test:int` | The console as a user sees it |
| E2E | `k6/e2e.js`, `k6/scenarios/` | `cluster k6 e2e` | The deployed k3d cluster |

## Writing tests

- **TEST-10** Keep a test module for tests only. Put fixtures in `sync/tests/fixtures/` and test-double dataclasses in `sync/tests/fixtures/types.py`. Put builders in the module of `sync/tests/helpers/` for their area (`core`, `state`, `dbos`, `ducklake`, `routing`, `rbac` or `console`). [must, review]
- **TEST-11** Merge small test files that cover one module. [should, review]
- **TEST-12** Test your own code and no code of a third party, such as DBOS, PostgREST, Keycloak, Kubernetes, Redis, a cloud SDK or a browser engine. [must, review]
- **TEST-13** Stub the call at the edge of your code, and test what your code does with the answer. [must, review]
- **TEST-14** Use table-driven cases with an `id` that names the scenario. Use Hypothesis only for invariants over generated inputs. [must, review]
- **TEST-15** Use `pytest-bdd` for integration behavior that crosses the database, the cache or the storage. [should, review]
- **TEST-16** Put feature files in `sync/tests/integration/features/` and step definitions in `sync/tests/integration/steps/`. [must, review]
- **TEST-17** Assert exact values. Don't use a substring check, except a regex against generated text when an exact assertion is brittle. [must, review]
- **TEST-18** Don't assert the full contents of a registry that grows. [must, review]
- **TEST-19** Don't test field storage, defaults, or "no exception was raised." [must, review]
- **TEST-20** Fix a flaky test at its cause, and add a test that reproduces it. Never add a retry. [must, review]

```python
@pytest.mark.parametrize(
    ("selection", "expected"),
    [
        pytest.param(RangeSelection(partition_id="10", column="id", lower=10, upper=20),
                     '("id" >= 10 AND "id" < 20)', id="range"),
    ],
)
def test_renders_exact_predicate(selection, expected):
    assert render(partition_condition(partition_for(selection))) == expected
```

A test module that has only tests, and a helper that lives in a module of `helpers/`:

```text
✅ sync/tests/helpers/state.py      def build_plan(...)
✅ sync/tests/unit/test_plan.py     def test_plan_groups_changed_tables(): ...
❌ sync/tests/unit/test_plan.py     def build_plan(...)   # a builder in a test module
```

<!-- nav:start -->
---

[Previous: Logs](logs.md) | [Next: k6 files](k6.md) | [Index](index.md) | [Home](../../index.md)
<!-- nav:end -->
