# k6 commands

The `cluster k6` command runs the load and end-to-end suites. The table gives the purpose of each command.

| Command             | Purpose                                                    |
| ------------------- | ---------------------------------------------------------- |
| `cluster k6 e2e`    | Validates the deployed release: sync, row-level security (RLS), routing, cache, snapshots, topology, runs, schedule, new schemas, tokens, and failover. |
| `cluster k6 e2e --build` | Builds and deploys fresh local images before validation. |
| `cluster k6 e2e --scenarios cache,access` | Runs only the named scenarios. |
| `cluster k6 smoke`  | Runs one virtual user for 40 seconds to check the setup.  |
| `cluster k6 load`   | Ramps to a configurable peak and keeps it. |

The load profile derives its rate from `K6_PEAK_RATE`, which defaults to 25 iterations per second. It ramps through 25%, 50%, 75%, and 100% of the peak, then keeps the peak before ramping down. Because the arrival-rate executor counts iterations and a fallback pair makes two requests, the profile converts the target request rate into an iteration rate internally.

The load test uses these terms:

| Term | Meaning |
| --- | --- |
| Selective lookup | A read of `big_table` that filters on one `id` or one `unit_id`. |
| Heavy query | A read of a route that the suite marks as heavy. It uses a random limit of 1,000 to 2,000 rows. |
| Pinned read | A read at an older DuckLake snapshot, with the header `X-DuckLake-Snapshot`. |
| Bottleneck case | A heavy query, a selective lookup or a pinned read. The `bottleneck` share sends these reads. |
| Capacity mode | A load run with `--capacity`. It has wider thresholds, more virtual-user headroom and larger shares. |
| Cold-cache run | A load run with a short cache TTL, such as `--cache-ttl 5`, so most reads miss the cache. |

Every iteration is one of these, with real Keycloak tokens and no write:

| Share | Traffic | Path |
| --- | --- | --- |
| The rest (92%, or 84% in capacity mode) | Data reads of the roles `dp_api` (`api`, `api_no_policy`): row filters, aggregates, JSON filters, sorted scans, fallback partitions, selective lookups and reads at a pinned snapshot. | Gateway, proxy, PostgREST |
| 1% each (5% each in capacity mode) | A fallback pair (a read and its cached repeat), and a bottleneck case: heavy queries, lookups and pinned reads (`K6_FALLBACK_SHARE`, `K6_BOTTLENECK_SHARE`). | Gateway, proxy, PostgREST |
| 5% | Console reads, as the web app sends them with the tokens of `console` (70%) and `admin` (30%): `overview`, `runs`, `tasks`, `logs`, `errors`, `health`, `synced`, `schedules`, `queues`, `schemas`, `tables`, and `table_files` for the admin only (`K6_CONSOLE_SHARE`). | PostgREST, `Accept-Profile: console` |
| 1% | A read of `access_policy` with the token of `policy` (`K6_POLICY_SHARE`). | Gateway, proxy, PostgREST |

The setup reads every console route once with each client that may read it, and stops the run if one fails. The load test never changes the configuration, starts a run, or writes the access policy. Only the setup seeds the access policy rows that the data reads need. The console reads go to `POSTGREST_URL` directly, as the web app does, so a generator outside the cluster needs a port-forward to PostgREST and `POSTGREST_URL=http://localhost:3000`. The gates `console_duration_ms` (p95 under 1 s) and `policy_duration_ms` (p95 under 500 ms) are first guesses. Measure a run, then set them.

Use flags to control the same scenario. The command uses a 300-second production-shaped cache time to live (TTL) by default and applies it to the deployed proxy before the run:

```nu
devenv shell -- nu scripts/cluster.nu k6 load \
  --peak-rate 100 \
  --cache-ttl 300 \
  --shares bottleneck=0.01 fallback=0.01
```

Use `--cache-ttl 5 --report-only` for the non-gating cold-cache stress run. Use `--capacity` with a higher peak and wider thresholds. The `bottleneck` share selects bottleneck cases. The `fallback` share selects configured-source fallback pairs. Staging capacity runs can use a higher peak from a generator outside the cluster host.

`cluster k6 smoke` and `cluster k6 load` wait until the Cluster has a primary and a standby, and PgDog and PostgREST are available. Before k6 starts, the command creates a one-off Job from the Helmfile-managed `manifests-sync-trigger` CronJob. This runs one normal DBOS sync to restore data that an earlier E2E run removed.

- [Thresholds](thresholds.md) — Performance thresholds and the limits of one generator.
- [E2E layout](e2e.md) — The layout of the end-to-end suite.

- [E2E scenarios](scenarios.md) — What the end-to-end scenarios check and how to run the suite.

<!-- nav:start -->
---

[Previous: Access the API](../api.md) | [Next: Thresholds](thresholds.md) | [Index](../index.md) | [Home](../../index.md)
<!-- nav:end -->
