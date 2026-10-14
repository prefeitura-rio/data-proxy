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
      scripts.migrate.exec = ''nu ${config.devenv.root}/scripts/migrate.nu "$@"'';

      git-hooks.hooks = {
        ruff.enable = true;
        ruff-format.enable = true;
      };

      tasks = {
        "test:sync" = {
          cwd = "sync";
          exec = ''
            set -e
            (cd engine && uv run --frozen pytest tests/unit)
            (cd console && uv run --frozen pytest)
          '';
          execIfModified = [
            "sync/**/*.py"
            "sync/pyproject.toml"
            "sync/uv.lock"
            "sync/.env.test"
            "sync/engine/tests/files"
            "sync/engine/tests/templates"
            "sync/engine/src/engine/templates"
          ];
        };
        "lint:sync" = {
          cwd = "sync";
          exec = ''
            uv run --frozen ruff check */src */tests
            (cd engine && basedpyright)
            (cd console && basedpyright)
            uv run --frozen pyrefly check
            uv run --frozen complexipy */src */tests
            (cd engine && uv run --frozen vulture --min-confidence 60 src tests)
            (cd console && uv run --frozen vulture --min-confidence 60 src tests)
          '';
          execIfModified = [
            "sync/**/*.py"
            "sync/pyproject.toml"
            "sync/*/pyproject.toml"
            "sync/uv.lock"
            "sync/complexipy-snapshot.json"
          ];
        };
        "lint:sql" = {
          exec = ''
            sqlfluff lint --dialect postgres sync/engine/src/engine/templates/postgres helm/files/templates sync/engine/tests/templates/postgres
            sqlfluff lint --dialect duckdb sync/engine/src/engine/templates/duckdb sync/engine/tests/templates/duckdb sync/engine/tests/templates/bigquery
            sqlfluff lint --dialect bigquery sync/engine/src/engine/templates/bigquery
          '';
          execIfModified = [
            ".sqlfluff"
            ".sqlfluffignore"
            "helm/files/templates/**/*.sql"
            "sync/**/*.sql"
          ];
        };
      };

      enterTest = ''
        set -e
        (cd ${config.devenv.root}/sync/engine && uv run --frozen pytest)
        (cd ${config.devenv.root}/sync/console && uv run --frozen pytest)
      '';
    };
  };
}
