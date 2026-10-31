{ lib, pkgs, ... }:
{
  profiles.sync-unit = {
    extends = [
      "base"
      "python"
    ];
    module = {
      key = "data-proxy-sync-unit";
      imports = [
        (
          { config, utils, ... }:
          let
            serverPort = config.processes.pg.ports.main.value or 15432;
            storagePort = config.processes.s3.ports.main.value or 18333;
          in
          {
            stdenv = lib.mkDefault pkgs.stdenvNoCC;

            env = lib.mapAttrs (_: lib.mkDefault) {
              TEST_POSTGRES_PORT = toString serverPort;
              TEST_POSTGRES_CONTAINER = "data-proxy-test-pg";
              TEST_S3_ENDPOINT = "127.0.0.1:${toString storagePort}";
              TEST_S3_ACCESS_KEY = utils.credentials.storage.admin.accessKey;
              TEST_S3_SECRET_KEY = utils.credentials.storage.admin.secretKey;
            };

            files."sync/.env.test".text = ''
              S3_BUCKET=test-bucket
              PG_DATABASE_URL=${
                utils.postgresUrl {
                  database = "test";
                  inherit serverPort;
                }
              }
              SCHEMA_WRITERS={"writers":{"test":"postgresql://test"}}
              DBOS_SYSTEM_DATABASE_URL=${
                utils.postgresUrl {
                  database = "test";
                  inherit serverPort;
                }
              }
              STREAM_INTERVAL_SECONDS=0.01
              DUCKLAKE_CATALOG_URL=${
                utils.postgresUrl {
                  database = "ducklake";
                  inherit serverPort;
                }
              }
              S3_ACCESS_KEY=${utils.credentials.storage.admin.accessKey}
              S3_SECRET_KEY=${utils.credentials.storage.admin.secretKey}
            '';

            packages = with pkgs; [ sqlfluff ];

            git-hooks.hooks = {
              ruff.enable = true;
              ruff-format.enable = true;
            };

            tasks = {
              "sync:test:unit" = utils.nuTask {
                description = "Run the sync unit tests that need no service.";
                after = [ "devenv:files" ];
                cwd = "sync";
                exec = ''
                  uv run --frozen pytest tests/unit -m "not postgres"
                '';
                execIfModified = utils.mkInputs (
                  [
                    "sync/**/*.py"
                    "sync/pyproject.toml"
                    "sync/uv.lock"
                    "sync/tests/templates/**/*.sql"
                    "helm/files/config/rbac.toml"
                  ]
                  ++ utils.mkGlobs "sync/tests/files" [
                    "parquet"
                    "csv"
                  ]
                  ++ utils.mkGlobs "sync/src/sync/templates" [ "sql" ]
                  ++ utils.mkGlobs "helm/files/templates" [ "sql" ]
                  ++ utils.mkGlobs "proxy/tests/unit/fixtures" [ "json" ]
                );
              };
              "sync:lint:py" = utils.nuTask {
                description = "Lint and type check the sync code.";
                cwd = "sync";
                exec = ''
                  uv run --frozen ruff check src tests
                  uv run --frozen ruff format --check src tests
                  uv run --frozen pyrefly check
                  uv run --frozen complexipy src tests
                  uv run --frozen vulture --min-confidence 60 src tests
                '';
                execIfModified = utils.mkInputs [
                  "sync/**/*.py"
                  "sync/pyproject.toml"
                  "sync/uv.lock"
                  "sync/complexipy-snapshot.json"
                ];
              };
              "sync:lint:sql" = utils.nuTask {
                description = "Lint the SQL templates and the console migrations.";
                exec = ''
                  sqlfluff lint --dialect postgres sync/src/sync/templates/postgres helm/files/templates sync/tests/templates/postgres console/migrations
                  goose -dir console/migrations validate
                  sqlfluff lint --dialect duckdb sync/src/sync/templates/duckdb sync/tests/templates/duckdb sync/tests/templates/bigquery
                  sqlfluff lint --dialect bigquery sync/src/sync/templates/bigquery
                '';
                execIfModified = utils.mkInputs [
                  ".sqlfluff"
                  ".sqlfluffignore"
                  "helm/files/templates/**/*.sql"
                  "sync/**/*.sql"
                  "console/migrations/*.sql"
                ];
              };
            };
          }
        )
      ];
    };
  };

  profiles.sync = {
    extends = [ "sync-unit" ];
    module = {
      imports = [
        ../nix/stack/sync.nix
        ../nix/stack/pg.nix
        ../nix/stack/s3.nix
      ];

      scripts.seed = {
        description = "Seed synthetic BigQuery data for the sync service.";
        exec = ''
          uv run --project sync python scripts/seed.py "$@"
        '';
      };

      enterTest = ''
        set -e
        uv run --directory sync --frozen pytest tests/integration --numprocesses auto --maxprocesses 4
        uv run --directory sync --frozen pytest tests/unit -m postgres --numprocesses auto --maxprocesses 4
      '';
    };
  };
}
