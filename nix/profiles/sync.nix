{
  pkgs,
  lib,
  config,
  ...
}:
{
  profiles.sync = {
    extends = [ "base" ];
    module = {
      imports = [
        ../processes/postgres.nix
        ../processes/postgrest.nix
        ../services/redis.nix
        ../services/silo.nix
      ];

      env = {
        UV_LINK_MODE = "copy";
        UV_PROJECT_ENVIRONMENT = lib.mkForce "${config.devenv.root}/sync/.venv";
      };

      packages = with pkgs; [
        goose
        minijinja
        postgresql_17
        sqlfluff
      ];

      languages.python = {
        enable = true;
        package = pkgs.python314;
        lsp.package = pkgs.basedpyright;
        uv.enable = true;
      };

      scripts.seed.exec = ''uv run --project sync python scripts/seed.py "$@"'';

      git-hooks.hooks = {
        ruff.enable = true;
        ruff-format.enable = true;
      };

      tasks = {
        "test:sync" = {
          cwd = "sync";
          exec = ''
            set -e
            uv run --frozen pytest tests/unit
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
        "lint:sync" = {
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
        "lint:sql" = {
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

      enterTest = ''
        set -e
        (cd ${config.devenv.root}/sync && uv run --frozen pytest)
      '';
    };
  };
}
