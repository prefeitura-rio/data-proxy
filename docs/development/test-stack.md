# Test stack

The integration suites run on a stack of local processes that devenv starts. This page describes the processes of each profile, their ports and how to add a test container. [Tests](tests.md) says how to run the suites.

The `sync` profile starts these processes:

- PostgreSQL, built from `Containerfile.pg` and run on the host network. Install Podman first, or install Docker and set `CONTAINER_RUNTIME=docker`. The build needs network for the base image. The engine keeps the layer cache, so the first run is slow and later runs only check the layers. The image is `localhost/data-proxy-test-pg:dev`.
- SeaweedFS for S3.

The `proxy` profile starts OpenResty with a mock PostgREST and Valkey. It needs no container engine.

The `console` profile starts these parts:

- the services of the `sync` profile
- Keycloak, from the test image of `Containerfile.kc` that `kc:build:image` builds once, and PostgREST
- the console with its gateway
- a worker for the `control` queue

It runs the browser tests of the console. [Frontend](frontend.md) describes it. A full run takes about one minute.

Every process asks devenv for its ports with `ports.<name>.allocate`. A port keeps its base number when it's free and moves up when it's busy. The setup passes the real number to the tests in environment variables.

| Suite | Base ports |
| --- | --- |
| `sync` | 15432 (PostgreSQL), 18333, 19333, 18082 and 18888 (SeaweedFS) |
| `console` | 15432 (PostgreSQL), 18333, 19333, 18082 and 18888 (SeaweedFS), 18180 (Keycloak), 13000 (PostgREST), 13001 (gateway), 13002 (notebooks), 13004 (console) |
| `proxy` | 18080, 18090, 18100 and 18110 (the four proxies), 18081, 18091, 18101 and 18111 (mock PostgREST), 18083, 18093, 18103 and 18113 (admin), 18399 (the Redis that the Redis-down proxy uses), 18379 (Valkey) |

The four proxies start one after another, because each `lx exec` rewrites `proxy/lux.lock` and two at the same time can read a half-written file.

The `default` profile runs the three suites in one `devenv test`.

devenv runs the task `sync:setup:db` (`scripts/db.nu`) before it starts pytest. If the task fails, the run stops and shows the error of the task.

The PostgreSQL image installs the `ducklake` and `postgres` extensions while it builds. A cached build layer keeps the version that it first downloaded. The sync worker writes DuckLake 1.0 catalogs, and `ducklake_catalog` in `sync/tests/fixtures/db.py` writes the test catalog with the same DuckDB. An image with an older `ducklake` extension fails the DuckLake tests. Rebuild with `podman build --no-cache` to refresh the extension.

On the GitHub `ubuntu-latest` runner (image `20261004.327.1`), Podman is 4.9.3. A cold build of the test image takes about 35 s. A cold `devenv --profile sync test` run takes about 60 s. CI therefore keeps no layer cache.

PostgreSQL starts empty on every run. The other services keep their files in `.devenv/test-state` for `devenv test` and in `.devenv/state` for `devenv up`. To start the object store empty, run `devenv --profile sync tasks run s3:clean:data`. The task deletes the SeaweedFS data folder and creates it again.

To add another test container, declare `processes.<name> = utils.mkProcess { command = utils.podmanRun { ... }; } // { ready = ...; }`, as `nix/stack/pg.nix` does. Build an image in a task that runs `before` the process, and set `ready` to a command that runs in the container. For a one-shot step after the container is ready, declare a task with `after = [ (utils.processTask "<name>") ]` and `wantedBy` the same process. `podmanRun` uses the host Podman.

<!-- nav:start -->
---

[Previous: Tests](tests.md) | [Next: Sync tests](sync-tests.md) | [Index](tests.md) | [Home](../index.md)
<!-- nav:end -->
