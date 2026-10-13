{
  pkgs,
  lib,
  config,
  inputs,
  ...
}:
let
  nuLibDirs = "${config.devenv.root}/scripts";
in
{
  name = "data-proxy";

  profiles = {
    base.module = {
      env = {
        LD_LIBRARY_PATH = lib.makeLibraryPath [ pkgs.stdenv.cc.cc ];
        NU_LIB_DIRS = nuLibDirs;
      };
    };

    quality = {
      extends = [ "base" ];
      module = {
        env = {
          UV_LINK_MODE = "copy";
          LPEG_TYPES = "${inputs.lpeg-types}/library";
          PENLIGHT_TYPES = "${inputs.penlight-types}/library";
          UV_PROJECT_ENVIRONMENT = "${config.devenv.root}/sync/.venv";
        };

        packages = with pkgs; [
          actionlint
          hadolint
          helmfile
          kubeconform
          lua-language-server
          lux-cli
          minijinja
          nu-lint
          nushell
          sqlfluff
          vale
        ];

        languages = {
          lua = {
            enable = true;
            package = pkgs.luajit;
            lsp.package = pkgs.lua-language-server;
          };
          helm = {
            enable = true;
            plugins = [ "helm-unittest" ];
          };
          python = {
            enable = true;
            package = pkgs.python314;
            lsp.package = pkgs.basedpyright;
            uv.enable = true;
          };
        };

        tasks = {
          "dp:lint:ci" = {
            exec = "actionlint .github/workflows/*.yaml";
            execIfModified = [
              ".github/workflows"
              ".github/actions"
            ];
          };
          "dp:lint:sync" = {
            cwd = "sync";
            exec = ''
              uv run ruff check src/ tests/
              basedpyright src/ tests/
              uv run complexipy src/ tests/
              uv run vulture src/ tests/ --min-confidence 90
            '';
            execIfModified = [
              "sync/**/*.py"
              "sync/pyproject.toml"
              "sync/uv.lock"
            ];
          };
          "dp:lint:nu" = {
            package = pkgs.nushell;
            exec = builtins.readFile ./scripts/nu-lint.nu;
            execIfModified = [
              "helm/files/**/*.nu"
              "scripts/**/*.nu"
              ".nu-lint.toml"
            ];
          };
          "dp:lint:containers" = {
            exec = ''
              hadolint Containerfile.sync
              hadolint Containerfile.postgres
              hadolint Containerfile.jobs
              hadolint Containerfile.proxy
            '';
            execIfModified = [
              ".hadolint.yaml"
              "Containerfile.sync"
              "Containerfile.postgres"
              "Containerfile.jobs"
              "Containerfile.proxy"
            ];
          };
          "dp:lint:sql" = {
            exec = ''
              sqlfluff lint --dialect postgres sync/src/sync/templates/postgres helm/files/templates sync/tests/templates/postgres
              sqlfluff lint --dialect duckdb sync/src/sync/templates/duckdb sync/tests/templates/duckdb sync/tests/templates/bigquery
              sqlfluff lint --dialect bigquery sync/src/sync/templates/bigquery
            '';
            execIfModified = [
              ".sqlfluff"
              ".sqlfluffignore"
              "helm/files/templates/**/*.sql"
              "sync/**/*.sql"
            ];
          };
          "dp:lint:helm" = {
            exec = "helm lint helm/ -f helm/ci/test-values.yaml";
            execIfModified = [ "helm" ];
          };
          "dp:proxy:rocks" = {
            before = [ "devenv:enterShell" ];
            package = pkgs.nushell;
            env.NU_LIB_DIRS = nuLibDirs;
            exec = builtins.readFile ./scripts/lux.nu;
            execIfModified = [
              "proxy/lux.toml"
              "proxy/lux.lock"
              "scripts/lux.nu"
            ];
          };
          "dp:lint:proxy" = {
            after = [ "dp:proxy:rocks" ];
            cwd = "proxy";
            exec = ''
              lx --lua-version jit lint
              lua-language-server --check . --configpath .luarc.json --logpath .lux/luals-log --checklevel=Hint
            '';
            execIfModified = [
              "proxy/**/*.lua"
              "proxy/lux.lock"
              "proxy/.luarc.json"
              "proxy/.luacheckrc"
            ];
          };
          "dp:test:sync" = {
            cwd = "sync";
            exec = "uv run pytest";
            execIfModified = [
              "sync/**/*.py"
              "sync/**/*.feature"
              "sync/src/sync/templates/**"
              "sync/tests/templates/**"
              "sync/tests/files/**"
              "sync/.env.test"
              "sync/pyproject.toml"
              "sync/uv.lock"
              "helm/files/templates/**/*.sql"
            ];
          };
          "dp:test:proxy" = {
            after = [ "dp:proxy:rocks" ];
            cwd = "proxy";
            exec = "lx --lua-version jit test";
            execIfModified = [
              "proxy/**/*.lua"
              "proxy/tests/unit/fixtures/**"
              "proxy/lux.toml"
              "proxy/lux.lock"
            ];
          };
          "dp:test:helm" = {
            package = pkgs.nushell;
            exec = builtins.readFile ./scripts/helm.nu;
            execIfModified = [
              "helm"
              "scripts/helm.nu"
            ];
          };
          "dp:lint:docs" = {
            exec = ''
              vale sync --no-exit
              vale docs/ README.md STYLEGUIDE.md sync/src/sync/ scripts/
            '';
            execIfModified = [
              "docs"
              "README.md"
              "STYLEGUIDE.md"
              "sync/src/**/*.py"
              "scripts"
              ".vale.ini"
              ".vale/styles"
            ];
          };
          "dp:lint".after = [
            "dp:lint:ci"
            "dp:lint:sql"
            "dp:lint:sync"
            "dp:lint:helm"
            "dp:lint:proxy"
            "dp:lint:nu"
            "dp:lint:containers"
            "dp:lint:docs"
          ];
          "dp:test".after = [
            "dp:test:sync"
            "dp:test:helm"
            "dp:test:proxy"
          ];
        };
      };
    };

    default = {
      extends = [ "quality" ];
      module = {
        env.KUBECONFIG = "k3d/.kubeconfig";

        packages = with pkgs; [
          ast-grep
          kubecolor
          kubectl
          k6
          k3d
          seaweedfs
          (google-cloud-sdk.withExtraComponents (
            with google-cloud-sdk.components; [ gke-gcloud-auth-plugin ]
          ))
        ];

        scripts =
          let
            nushell = file: {
              package = pkgs.nushell;
              binary = "nu";
              exec = file;
            };
          in
          {
            ci = nushell ./scripts/ci.nu;
            cluster = nushell ./scripts/cluster.nu;
            token = nushell ./scripts/token.nu;
            seed.exec = ''uv run --project sync python scripts/seed.py "$@"'';
          };

        git-hooks.hooks = {
          ruff.enable = true;
          ruff-format.enable = true;
          ripsecrets.enable = true;
          lx-fmt = {
            enable = true;
            name = "lx fmt";
            entry = "bash -c 'cd proxy && lx --lua-version jit fmt'";
            language = "system";
            files = "^proxy/.*\\.lua$";
            pass_filenames = false;
          };
        };
      };
    };

    helm = {
      extends = [ "base" ];
      module = {
        packages = with pkgs; [
          gh
          kubernetes-helm
          kubeconform
        ];

        languages.helm = {
          enable = true;
          plugins = [ "helm-unittest" ];
        };
      };
    };
  };
}
