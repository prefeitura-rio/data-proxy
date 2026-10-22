{ pkgs, ... }:
{
  profiles.sync = {
    extends = [
      "base"
      "python"
    ];
    module =
      { utils, ... }:
      {
        imports = [
          ../nix/processes/sync.nix
          ../nix/processes/postgres.nix
          ../nix/processes/seaweedfs.nix
        ];

        files."sync/.env.test".text = ''
          S3_BUCKET=test-bucket
          PG_DATABASE_URL=${utils.postgresUrl { database = "test"; }}
          SCHEMA_WRITERS={"writers":{"test":"postgresql://test"}}
          DBOS_SYSTEM_DATABASE_URL=${utils.postgresUrl { database = "test"; }}
          STREAM_INTERVAL_SECONDS=0.01
          DUCKLAKE_CATALOG_URL=${utils.postgresUrl { database = "ducklake"; }}
        '';

        packages = with pkgs; [ sqlfluff ];

        languages.python.lsp.package = pkgs.basedpyright;

        scripts.seed = {
          description = "Seed synthetic BigQuery data for the sync service.";
          exec = ''
            uv run --project sync python scripts/seed.py "$@"
          '';
        };

        git-hooks.hooks = {
          ruff.enable = true;
          ruff-format.enable = true;
        };

        tasks = {
          "test:sync" = utils.nuTask {
            description = "Run the sync unit tests that need no service.";
            after = [ "devenv:files" ];
            cwd = "sync";
            exec = ''
              uv run --frozen pytest tests/unit -m "not postgres"
            '';
            execIfModified = [
              "sync/**/*.py"
              "sync/pyproject.toml"
              "sync/uv.lock"
              "sync/.env.test"
              "sync/tests/files"
              "sync/tests/templates"
              "sync/src/sync/templates"
            ];
          };
          "lint:sync" = utils.nuTask {
            description = "Lint and type check the sync code.";
            cwd = "sync";
            exec = ''
              uv run --frozen ruff check src tests
              basedpyright
              uv run --frozen pyrefly check
              uv run --frozen complexipy src tests
              uv run --frozen vulture --min-confidence 60 src tests
            '';
            execIfModified = [
              "sync/**/*.py"
              "sync/pyproject.toml"
              "sync/uv.lock"
              "sync/complexipy-snapshot.json"
            ];
          };
          "lint:sql" = utils.nuTask {
            description = "Lint the SQL templates and the console migrations.";
            exec = ''
              sqlfluff lint --dialect postgres sync/src/sync/templates/postgres helm/files/templates sync/tests/templates/postgres console/migrations
              goose -dir console/migrations validate
              sqlfluff lint --dialect duckdb sync/src/sync/templates/duckdb sync/tests/templates/duckdb sync/tests/templates/bigquery
              sqlfluff lint --dialect bigquery sync/src/sync/templates/bigquery
            '';
            execIfModified = [
              ".sqlfluff"
              ".sqlfluffignore"
              "helm/files/templates/**/*.sql"
              "sync/**/*.sql"
              "console/migrations/*.sql"
            ];
          };
        };

        enterTest = "uv run --directory sync --frozen pytest";
      };
  };
}
