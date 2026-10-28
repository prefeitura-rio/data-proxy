# Changelog
All notable changes to this project will be documented in this file. See [conventional commits](https://www.conventionalcommits.org/) for commit guidelines.

- - -
## helm-v2.3.0 - 2026-10-28

### Features

- **sync**: run the aggregate of a request inside DuckDB (c1ede39)
- **proxy**: send aggregate selects to the aggregate function of the table (13d5ecd)

### Fixes

- **sync**: pass the type check and the SQL lint for the aggregate function (bb4c3be)
- **proxy**: narrow the aggregate response before the null check (2d7aa4e)


- - -

## helm-v2.2.0 - 2026-10-28

### Features

- **rbac**: read every row of a schema with the access policy grant * and * (b5fd74d)

### Fixes

- **helm**: pin the sync image to the b5fd74d build (00b9dd6)


- - -

## helm-v2.1.0 - 2026-10-28

### Features

- **rbac**: read every row of one schema with schema:* in the schemas claim (2bd27ec)


- - -

## helm-v2.0.2 - 2026-10-28

### Fixes

- **helm**: pin the sync image to the 8f81ed6 build (a48d687)
- **sync**: validate x-ducklake-filter without a savepoint (8f81ed6)


- - -

## helm-v2.0.1 - 2026-10-28

### Fixes

- **helm**: pin the images to the 42ea387 build (#10) (36a14b8)
- **ci**: resolve the image tag that the latest tag points to (#9) (42ea387)


- - -

## helm-v2.0.0 - 2026-10-27

### Breaking changes

- **sources**: route table reads through pluggable sources in PostgreSQL (4396b11)
- **proxy**: rename the nginx component to proxy and keep it cache-only (ed0304e)
- **sync**: move orchestration from Redis streams to DBOS (5a485b3)
- rename pipeline and proxy components (1e582b5)
- rename sql/ to templates/ and split maintenance templates (d1c9fd4)
- **tests**: split steps, remove metadata, fill integration gaps (8b1b80f)
- **integration**: migrate integration tests to pytest-bdd (909ce82)
- **database**: move maintenance cleanup into procedures (21e34e4)
- **package**: rename Python package from dp to data_proxy (0bd38d8)
- **sync**: move orchestration from Redis streams to DBOS (b7e4a15)

### Features

- **console**: change the cron of a schedule in the Sync tab (44537c9)
- **console**: scope the schedules by schema and add set_schedule (72c83e6)
- **sync**: create one schedule for each schema (2964f0f)
- **console**: send a content security policy and the other security headers (a19f118)
- **helm**: add optional network policies for the proxy, PostgREST, PgDog and the web pages (f3e285a)
- **helm**: run every pod as a non-root user with a restricted security context (677228c)
- **console**: open the notebook with one cell for each chosen table and drop the notebook tests (dc39edb)
- **console**: add toasts, submit states, filter forms, live data and a rebuilt run log viewer (60ef870)
- **sync**: log each dump partition and each table that a run publishes (f7dd205)
- **proxy**: push columns, order and limit into the DuckLake read (19f4c31)
- **helm**: back up the database through the Barman Cloud plugin and limit the WAL of a slot (f779ca2)
- **console**: serve the console under /console of the API host (57aa0f5)
- **notebooks**: follow the theme of the console (e65e903)
- **notebooks**: query with polars and preview the first 50 rows of each table (01ec4fc)
- **rbac**: let the schemas claim name every schema with * (d6ebaed)
- **scripts**: name the role that Keycloak writes as a list in text (70166ac)
- **scripts**: print the user profile attribute that the schemas claim needs (688f1a5)
- **scripts**: print the Keycloak console client from rbac.toml and check a token (3f6896e)
- **notebooks**: show the tables and their first rows in the example (b1cd379)
- **console**: use a database icon as the favicon (2b20c27)
- **console**: stream the logs of a run with a Live switch (4f63e26)
- **nix**: open the console in the browser after up (8b9195d)
- **console**: add the light and dark themes and polish the pages (ade81ce)
- **nix**: check the links of the docs with a lint:links task (ab0d786)
- **helm**: delete the old DBOS workflows in the maintenance job (7febbcd)
- **console**: make the lifetime of the signed links a setting (0fae272)
- **nix**: scope each tool to its task and add docs build and serve tasks (fefd116)
- **ci**: release the Helm chart with cocogitto and write the changelog (44a4e1c)
- **scripts**: add docs.nu to write the roles table and the navigation footers (2f91138)
- **console**: add the web app with paging, run control, history and import preview (1932205)
- **console**: add run control, schedule control and import preview functions (5b6b680)
- **console**: add run schemas and config history migrations (6940c51)
- **sync**: add the control queue and cancel, resume and schedule workflows (50c2195)
- **rbac**: add control_sync permission (7098e69)
- **helm**: add the schema reader and signer key templates (b22c1d5)
- **helm**: add the console and notebooks deployments (123aef3)
- **sync**: add tasks to remove stale s3 identities (2e475cd)
- add tasks to sync service (620b857)
- **rbac**: create the roles and check them in the database (f6a8ecb)
- **scripts**: add the cluster upgrade command (da801f6)
- **chart**: provision and remove schemas in the provision script (683d5a9)
- **chart**: add suspended CronJobs as Job templates (4ad9259)
- **sync**: provision schemas at the start of every run (4d25431)
- **sync**: create Jobs from a CronJob with lightkube (b195fbe)
- **proxy**: add the rbac revision to the cache key (04f5a23)
- **console**: rebuild the console migrations on the rbac functions (102accb)
- **rbac**: add the rules file and its schema (fe1865a)
- **proxy**: let one request fetch an expired cache entry while the others wait (3801869)
- **chart**: tune PgDog, drop its lag rules and scaler, and take lagging standbys out of the read Service (44a3e94)
- **console**: list the parquet files of a table for the query tab, and add the console clients (80a89e4)
- **console**: scope runs and steps, add the overview view, and check provisioning before a run (a96290d)
- **console**: scope every view, table and run to the schemas of the token (a295208)
- **proxy**: send a read again when postgrest fails for a moment (287a3a4)
- **sync**: reload postgrest with notify after the standbys replayed the change (57c5792)
- **chart**: put pgdog in front of one postgrest and drop the ha mode (93e56bd)
- **chart**: wire the proxy reload and the config endpoint (2741243)
- **proxy**: refuse the admin profile and ask for the config with it (1c70232)
- **sync**: reload the proxy pods when the configuration changes (e1f378a)
- **console**: serve the proxy config and queue a reload on every change (18f6aa3)
- **chart**: back up the schemas that the console adds (79f4049)
- **proxy**: read table settings from a file that the provision job keeps current (d40949e)
- **chart**: provision the schemas that the console adds (dbdefb8)
- **chart**: migrate the console schema in a Job that runs in the sync image (47c1bd8)
- **sync**: load the sync configuration from the admin tables (47bce3d)
- **console**: serve the configuration as sync.json from one endpoint (bae83c5)
- **console**: model the sync configuration as tables (7775a61)
- **sync**: load the sync configuration from the active revision (4711f84)
- **console**: validate saved drafts with the sync model (ccfa9d4)
- **console**: let operators change queues and schedules (cf10636)
- **console**: let operators start sync runs from SQL (083db3f)
- **console**: let admins activate validated revisions (bbb7532)
- **console**: let editors write draft configurations (e0c1799)
- **console**: add the permission level roles and the viewer grants (d12f560)
- **console**: count run tasks and report schema objects in the admin views (1aeb213)
- **console**: add the admin views over DBOS and the sync state (0e424e1)
- **console**: scaffold the SvelteKit admin app and its devenv profile (73e5b3c)
- **console**: report which objects of a schema exist (d8374fe)
- **console**: read and write schemas and tables with per-resource ETags (572c1dd)
- **console**: change queue limits and schedules (f673172)
- **engine**: create the queues and the schedule only when they don't exist (90479af)
- **console**: start a sync run on demand (a15db35)
- **engine**: plan only the scope of an on-demand run (0bba551)
- **chart**: add the DuckLake catalog database, roles and secrets (377523b)
- **engine**: store the DuckLake catalog in PostgreSQL (aff1b8c)
- **console**: reload the proxies after a config change (96f4cf2)
- **chart**: pass the console address to the proxy and expose its admin port (60be870)
- **proxy**: load the settings from the console and reload them on request (aa54bd2)
- **engine**: load the sync config once per run from the console (ff53302)
- **console**: serve the exact sync.json of the active revision (981a111)
- **console**: stream the queues, the phases of a run and the active config revision (8415983)
- **console**: read and write the sync config as revisions through the API (e8de449)
- **console**: add goose migrations for the config revisions and the schema registry (1a9db6c)
- **console**: size the database pool for a session-mode pooler (3dcfed8)
- **console**: follow the log of a workflow as server-sent events (2f21737)
- **console**: list the queues with their depth and the schedules (225f64b)
- **engine**: write run logs to a DBOS stream with the same fields as the logger (351e29a)
- **engine**: run the DBOS worker on uvloop (dd0f3de)
- **console**: open a psycopg connection pool and check it in readiness (1ed7c93)
- **api**: register the route modules automatically (3e38919)
- **api**: show one sync run with its steps and child workflows (154bb34)
- **api**: list the sync runs from DBOS (10dd0ff)
- **api**: add the dashboard API app with typed state and health probes (6444bd0)
- **helm**: serve PostgREST metrics on an admin port (19d0efd)
- **helm**: add the in-flight limit, a larger proxy CPU request and a shorter pool wait (9888ff6)
- **proxy**: answer 503 when too many requests wait for PostgREST (0faf3be)
- **proxy**: count policy changes and key the cache by them (0febb96)
- **helm**: require the Redis host of the proxy (0b268bb)
- **proxy**: warn when the Redis cache fails (efd8078)
- **dev**: run the proxy suite as a devenv process (03da161)
- **dev**: run PostgREST, Silo, and Valkey as test processes (9de14e5)
- **dev**: run PostgreSQL as a test process built from Containerfile.postgres (da6eba8)
- **dev**: add mkPodmanProcess for container test processes (8e4f85f)
- **database**: render proxy-pushed filters into the DuckLake scan (c91fb78)
- **proxy**: parse supported PostgREST filters in the proxy (3a7ac51)
- **dev**: add sync --build, wire HA mode, and adapt local storage (c908e35)
- **helm**: restore instance catalogs from Litestream (bde0c9e)
- **sync**: refresh instance catalogs and apply DuckLake maintenance (1c9741c)
- **dev**: synchronize local images through helmfile (21ccc92)
- **dev**: manage local stack with k3d (910918b)
- **dev**: manage local stack with k3d and Helmfile (ce3a435)
- **k6**: add configurable local capacity profiles (c791b26)
- **ci**: release charts from Conventional Commits (2a61929)
- **helm**: recover shared DuckLake catalogs (93796a8)
- **proxy**: rename nginx to proxy, add HA retry and routing (afce300)
- **helm**: HA mode, autoscaling, resource limits, and pooler pin (c3b16ac)
- **sync**: write table state only after the reader reaches the snapshot (48be94f)
- **sync**: allow disabling the dump queue rate limit (8586a44)
- simplify RLS function templates and split DuckLake/BigQuery (3082273)
- add DBOS distributed workflow recovery (ee0997c)
- **helm**: expose tunable CNPG PostgreSQL parameters (f6771ee)

### Fixes

- **ci**: read the sync and jobs image tags with their real output names (#8) (ed06143)
- **console**: copy the notebooks page module into the image (ede3063)
- **nix**: do not run the browser opener in the console tests (9673752)
- **console**: lock the schema row when a row rule checks the claim (0af0058)
- **helm**: scale the proxy on CPU at 70% up to six pods and read its minimum from the values (a8d5705)
- **helm**: give the proxy a Redis host with the namespace, so its cache works (74dc8e4)
- **sync**: cut the change-feed function name where PostgreSQL cuts it (d452d6c)
- **sync**: reconcile the serving views when a run plans no change (e36436d)
- **helm**: stop the AWS client from adding checksum trailers that GCS refuses in the database backup (7cd9510)
- **security**: fix the findings of the SAST scan and ignore the test code (a09fa96)
- **sync**: save the state of each table right after its commit (fc4d7df)
- **sync**: cap a DuckLake row group at 32 MB so the publish of wide rows fits in memory (cf04320)
- **console**: show the no-access page for a token without a role instead of looping (20b68fe)
- **sync**: take small batches and send the first BigQuery requests one at a time (0c72156)
- **sync**: publish row groups of 10000 rows and give each DuckDB connection 1 GB (42e9e20)
- **sync**: give each DuckDB connection its own spill folder (9757da1)
- **sync**: write Parquet row groups of 10000 rows so a dump of wide tables fits in memory (89423af)
- **sql**: give the limits template its values for the linter (ecc5ea7)
- **sync**: set the DuckDB limits with their own template (b920850)
- **sync**: limit the memory and the threads of each DuckDB connection (102c0b3)
- **sync**: treat GEOMETRY and GEOMETRY with a CRS as one column type (23fc32e)
- **helm**: read the S3 credentials from s3.existingSecret (5a22fdb)
- **sync**: import datetime at run time for the run_sync workflow (60f783f)
- **console**: check the origin against ORIGIN, not the request address (270fdaf)
- **helm**: move the CNPG primary by a switchover when the pod changes (8dc1541)
- **sync**: install the OpenTelemetry logging package that DBOS needs for OTLP (624a574)
- **helm**: give the bucket check the Google key file (7044db5)
- **helm**: make the chart label valid for a version with build metadata (1908640)
- **ci**: keep the preview pin local when a newer pin exists (4752042)
- **nix**: start the console after postgres, storage, keycloak and postgrest (6361c46)
- **notebooks**: stop marimo from asking for a sandbox on a terminal (c794b43)
- **nix**: run the console and notebook builds before the processes start (e89437c)
- **nix**: wrap the long list in the docs link tests (b652a59)
- **nix**: wrap the long list in the docs link tests (606cfad)
- **sync**: format the console seed and check the format in lint:sync (d8f0995)
- **nix**: install the pnpm dependencies once for the console tasks (a3c6488)
- **helm**: test the image wiring without the pinned tags (ab06b07)
- **console**: read the log lines in pages under the PostgREST row cap (bef77aa)
- **ci**: run the pipeline when Containerfile.jobs changes (8c37213)
- **jobs**: update the pinned Debian packages (dd5d42c)
- **nix**: do not run the git hooks in the console tests (d774479)
- **helm**: make the scratch prefix a chart value (3b21510)
- **ci**: run SAST on main, publish the chart before the push and accept a release token (13c2d24)
- **sync**: default the user role to dp_api (2772038)
- **nix**: list every input of the notebook site build (5a46d40)
- **ducklake**: attach each metadata schema under its own alias (3bf9cae)
- **test**: run migrate.nu with the nushell that runs db.nu, not one from PATH (bf01651)
- **test**: start the test PostgreSQL as root so its entrypoint owns the data directory (1b56ff1)
- **ci**: run the ci script in the checks profile (e32d8a5)
- **console**: count the partitions of a table that has none, and name each console check (2387399)
- **chart**: let the policy writers join a static role that CNPG gives to the authenticator (1880b6d)
- **chart**: mount SQL templates in their directories, rename the policy trigger function, allow proxy reload (dd8e995)
- **e2e**: clear type errors left from the single postgrest topology (ef179b4)
- **e2e**: rename a clashing variable (cb03c63)
- **chart**: give each console level a statement timeout, close the reload functions to public, and drop the unused jobs grants (71c8b16)
- **chart**: run the pgdog binary as the container command (366d37c)
- **chart**: create the DuckDB secrets volume without schemas in the values (9505055)
- **console**: answer every error as problem+json (657c320)
- **console**: type the log stream so the strict checks pass (4fe61a1)
- **api**: hide the database error in the readiness answer and log it (f855a31)
- **ci**: let the change detection write to GITHUB_OUTPUT (edf9b8e)
- **ci**: compare the first push of a branch with main (8ea6eab)
- **helm**: limit 32 waiting requests in each proxy pod by default (8565624)
- **proxy**: wait at most 30 seconds for PostgREST (6766f08)
- **k6**: check the newest backup folder instead of today's date (e3984b8)
- **helm**: let the mesh drain a stopping PostgREST pod (7cc6f78)
- **helm**: replace the reader catalog in one step during a refresh (704514a)
- **k6**: expect the combined source for the partitioned table (5a6bdb9)
- **k6**: use the current names of the change feed and the HA variable (9f8410d)
- **k6**: stop repeating the cache database variable in the cleanup job (08a6747)
- **k6**: compare rows without depending on the key order (eb647ee)
- **k3d**: point the proxy cache at the local Valkey (2d8efe9)
- **k6**: call the renamed dl_latest_snapshot function (cf38a2e)
- **proxy**: read the request body before forwarding a write (9d0c4d2)
- **dev**: remove the catalogs through the podman user namespace (11f045e)
- **dev**: remove the local catalogs when the cluster goes down (a558c80)
- **dev**: build local images in docker format so SHELL applies (e44a90a)
- **dev**: pass the script directory to the cluster and token wrappers (95f5f30)
- **proxy**: copy forwarded headers before adding the filter header (cb46e60)
- **sync**: quote and wrap the columns in the rendered filter (7cd5fd4)
- **sync**: drop stale DuckLake helper functions (ece04ac)
- **dev**: keep Lux from leaving a reordered lux.lock (69a0ce4)
- **proxy**: send an empty body for write methods (e460929)
- **dev**: set NU_LIB_DIRS on the rocks task (24425f3)
- **k6**: drive the Lua proxy instead of webdis (cdfe23d)
- **proxy**: support njs preloaded table arrays (a17a047)
- **k6**: make the end-to-end and performance runs repeatable (e95ed64)
- **proxy**: standardize request logs and njs compatibility (73e7be2)
- **proxy**: remove unused error binding (22cb56b)
- **helm**: qualify DBOS writer endpoint (bcfb2b3)
- **helm**: bootstrap storage before consumers (6b48b91)
- **helm**: scale sync on DBOS workflows (b3ef66b)
- **helm**: restore backup and PostgREST init scripts (f233269)
- **k6**: preserve unique TestRun names (bcbb973)
- **sync**: refresh poolers after snapshot publication (201b980)
- **src**: adapters return void, maintenance procedures, and security fixes (7ece438)
- **helm**: quote psql variables in the maintenance and backup calls (9ff19fe)
- **templates**: render composables nested in lists and mappings (68204db)
- resolve DuckLake SQLite catalog lock contention with Litestream (80aae44)
- **build**: repair PostgreSQL image install (b4bf7dd)
- **nginx**: update empty-response cache assertion for one-hour TTL (673f99e)
- **code**: satisfy vale linter and fix queue rate limit key (cc4a31a)
- **scripts**: use list spread for external command arguments (f79025c)
- **scripts**: use data_proxy imports (8714991)
- **freshness**: preserve explicit empty partition failures (ac0cbce)
- **migrate**: reject empty schema lists (12109f1)
- **templates**: restore stale object cleanup query (4eb5629)
- **database**: grant maintenance procedures to jobs (fa65b42)
- **database**: install maintenance procedures during init (4dba1cc)
- **database**: clean access policies with stale objects (54e39a1)
- **sync**: avoid retrying system exceptions (8011849)
- **sync**: classify transient step retries (b2ca831)
- **observability**: export custom metrics over OTLP (08511e6)
- **chart**: wire DBOS worker controls and telemetry (46d2009)
- **sync**: record terminal workflow outcomes (371085c)
- **test**: align facade tests with current APIs (eda08cd)
- **migrate**: quote DBOS schedule schema (a3edab7)

### Performance Improvements

- **sync**: check a pinned snapshot with a catalog key lookup (ab8884b)
- **postgrest**: optimize typed DuckLake serving path (16a09c2)

### Build system

- **console**: add the console image (4279aee)
- **k6**: ship the scripts in the k6 image (81216e5)
- **devenv**: import profiles with import-tree and lint rules with taplo (8032780)
- **sync**: run the console migrations in the test setup and lint them (8a7a6af)
- **sync**: share common version bounds in the workspace root (b060d95)
- **console**: serve with granian on uvloop instead of uvicorn (783c70d)
- **sync**: make sync a uv workspace with console as a member (0d07480)
- **sync**: add fastapi, uvicorn and httpx for the API (0683ec3)

### Continuous Integration

- run the pipeline when a linter file of the root changes (7f530f5)
- **preview**: publish the chart as a preview version (bc75793)
- **preview**: build and pin the preview images (eb6a2e0)
- add the console tests job (c251ea5)
- run lint and tests on staging without building (faf0ba5)
- remove the manual E2E workflow (499494f)
- run the tests when the nix test stack changes (fdaafc9)
- add a manual E2E workflow that runs the k3d suites (29dbf13)
- run the sync and proxy suites as separate jobs (359be05)
- remove obsolete coverage enforcement (b33cd5d)
- **vale**: configure vale linter for docs and code (a3dce0a)


- - -

Changelog generated by [cocogitto](https://github.com/cocogitto/cocogitto).