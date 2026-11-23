{ ... }:
{
  profiles.sync = {
    extends = [
      "base"
      "python"
    ];
    module =
      {
        config,
        data,
        lib,
        pkgs,
        utils,
        ...
      }:
      let
        serverPort = config.processes.pg.ports.main.value or 15432;
        storagePort = config.processes.s3.ports.main.value or 18333;
      in
      {
        key = "data-proxy-sync";
        imports = [
          ../nix/services/postgres.nix
          ../nix/services/seaweedfs.nix
        ];

        env = lib.mapAttrs (_: lib.mkDefault) {
          TEST_POSTGRES_PORT = toString serverPort;
          TEST_POSTGRES_CONTAINER = utils.container "pg";
          TEST_S3_ENDPOINT = "127.0.0.1:${toString storagePort}";
          TEST_S3_ACCESS_KEY = data.credentials.storage.admin.accessKey;
          TEST_S3_SECRET_KEY = data.credentials.storage.admin.secretKey;
          S3_BUCKET = "test-bucket";
          S3_ACCESS_KEY = data.credentials.storage.admin.accessKey;
          S3_SECRET_KEY = data.credentials.storage.admin.secretKey;
          PG_DATABASE_URL = utils.postgresUrl {
            database = "test";
            inherit serverPort;
          };
          DBOS_SYSTEM_DATABASE_URL = utils.postgresUrl {
            database = "test";
            inherit serverPort;
          };
          DUCKLAKE_CATALOG_URL = utils.postgresUrl {
            database = "ducklake";
            inherit serverPort;
          };
          SCHEMA_WRITERS = ''{"writers":{"test":"postgresql://test"}}'';
          STREAM_INTERVAL_SECONDS = "0.01";
        };

        git-hooks.hooks = {
          sync-lint-py = utils.mkHook {
            profile = "sync";
            task = "sync:lint:py";
            files = "^sync/.*\\.py$";
          };
          sync-lint-fmt = utils.mkHook {
            profile = "sync";
            task = "sync:lint:fmt";
            files = "^sync/.*\\.py$";
          };
          sync-lint-sql = utils.mkHook {
            profile = "sync";
            task = "sync:lint:sql";
            files = "^(sync|helm/files/templates|migrations)/.*\\.sql$";
          };
        };

        tasks = {
          "sync:setup:db" =
            utils.mkTask {
              command = [
                "nu"
                "scripts/db.nu"
              ];
              packages = with pkgs; [
                kubernetes-helm
                uv
              ];
            }
            // {
              description = "Create the databases of the sync tests.";
              after = [ (utils.processTask "pg") ];
              wantedBy = [ (utils.processTask "pg") ];
            };
          "sync:lint:fmt" = utils.nuTask {
            description = "Check the format of the sync code.";
            cwd = "sync";
            exec = "uv run --frozen ruff format --check src tests";
            execIfModified = utils.mkInputs [
              "sync/**/*.py"
              "sync/pyproject.toml"
              "sync/uv.lock"
            ];
          };
          "sync:lint:py" = utils.nuTask {
            description = "Lint and type check the sync code.";
            cwd = "sync";
            exec = ''
              uv run --frozen ruff check src tests
              uv run --frozen pyrefly check
              uv run --frozen vulture --min-confidence 60 src tests
            '';
            execIfModified = utils.mkInputs [
              "sync/**/*.py"
              "sync/pyproject.toml"
              "sync/uv.lock"
            ];
          };
          "sync:lint:sql" = utils.nuTask {
            description = "Lint the SQL templates and the migrations.";
            exec = ''
              uv run --frozen --project sync sqlfluff lint --dialect postgres sync/src/sync/templates/postgres helm/files/templates sync/tests/templates/postgres migrations
              goose -dir migrations/console validate
              goose -dir migrations/ducklake validate
              uv run --frozen --project sync sqlfluff lint --dialect duckdb sync/src/sync/templates/duckdb sync/tests/templates/duckdb sync/tests/templates/bigquery
              uv run --frozen --project sync sqlfluff lint --dialect bigquery sync/src/sync/templates/bigquery
            '';
            execIfModified = utils.mkInputs [
              ".sqlfluff"
              ".sqlfluffignore"
              "sync/pyproject.toml"
              "sync/uv.lock"
              "helm/files/templates/**/*.sql"
              "sync/**/*.sql"
              "migrations/*/*.sql"
            ];
          };
        };

        scripts.seed = {
          description = "Seed synthetic BigQuery data for the sync service.";
          exec = ''
            uv run --project sync python scripts/seed.py "$@"
          '';
        };

        enterTest = ''
          set -e
          uv run --directory sync --frozen pytest tests/unit -m "not postgres"
          uv run --directory sync --frozen pytest tests/integration --numprocesses auto --maxprocesses 4
          uv run --directory sync --frozen pytest tests/unit -m postgres --numprocesses auto --maxprocesses 4
        '';
      };
  };
}
