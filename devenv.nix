{
  pkgs,
  lib,
  config,
  ...
}:
let
  crdSchema = "https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json";
in
{
  name = "data-proxy";

  profiles = {
    base.module = {
      env.LD_LIBRARY_PATH = lib.makeLibraryPath [ pkgs.stdenv.cc.cc ];
    };

    quality = {
      extends = [ "base" ];
      module = {
        env = {
          UV_LINK_MODE = "copy";
          UV_PROJECT_ENVIRONMENT = "${config.devenv.root}/.venv";
        };

        packages = with pkgs; [
          actionlint
          hadolint
          helmfile
          kubeconform
          minijinja
          nodejs
          nu-lint
          nushell
          sqlfluff
          typescript
          vale
        ];

        languages = {
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
          "dp:proxy:deps" = {
            exec = ''
              npm install --no-save @types/node njs-types peggy@4.2.0 >/dev/null
              node proxy/peggy/build.mjs
            '';
            execIfModified = [
              "devenv.nix"
              "devenv.lock"
              "node_modules/@types/node"
              "node_modules/njs-types"
              "proxy/peggy/filter.peggy"
              "proxy/peggy/build.mjs"
              "proxy/peggy/filter.generated.js"
              "proxy/peggy/filter.generated.d.ts"
            ];
          };
          "dp:lint:ci" = {
            exec = "actionlint .github/workflows/*.yaml";
            execIfModified = [
              ".github/workflows"
              ".github/actions"
              "devenv.nix"
              "devenv.lock"
              "devenv.yaml"
            ];
          };
          "dp:lint:py" = {
            exec = ''
              uv run ruff check src/ tests/
              uv run basedpyright src/ tests/
              uv run complexipy src/ tests/
              uv run vulture src/ tests/ --min-confidence 90
            '';
            execIfModified = [
              "src"
              "tests"
              "pyproject.toml"
              "uv.lock"
            ];
          };
          "dp:lint:nu" = {
            exec = "find helm/files scripts -name '*.nu' -exec nu-lint --config .nu-lint.toml {} +";
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
              "Containerfile.sync"
              "Containerfile.postgres"
              "Containerfile.jobs"
              "Containerfile.proxy"
            ];
          };
          "dp:lint:sql" = {
            exec = ''
              sqlfluff lint --dialect postgres src/data_proxy/templates/postgres helm/files/templates tests/templates/postgres
              sqlfluff lint --dialect duckdb src/data_proxy/templates/duckdb tests/templates/duckdb tests/templates/bigquery
              sqlfluff lint --dialect bigquery src/data_proxy/templates/bigquery
            '';
            execIfModified = [
              ".sqlfluff"
              ".sqlfluffignore"
              "helm/files/templates"
              "src/data_proxy/templates"
              "tests/templates"
            ];
          };
          "dp:lint:helm" = {
            exec = "helm lint helm/ -f helm/ci/test-values.yaml";
            execIfModified = [ "helm" ];
          };
          "dp:lint:proxy" = {
            exec = "tsc -p proxy";
            after = [ "dp:proxy:deps" ];
            execIfModified = [
              "proxy"
              "package.json"
              "package-lock.json"
            ];
          };
          "dp:test:py" = {
            exec = "uv run pytest";
            after = [ "dp:proxy:deps" ];
            execIfModified = [
              "src"
              "tests"
              "pyproject.toml"
              "uv.lock"
            ];
          };
          "dp:test:proxy" = {
            exec = ''
              node --test --experimental-test-coverage --test-coverage-lines=100 --test-coverage-include="proxy/parser.ts" proxy/parser.test.ts
              node --test proxy/proxy.test.ts
            '';
            after = [ "dp:proxy:deps" ];
            execIfModified = [ "proxy" ];
          };
          "dp:test:helm" = {
            exec = ''
              set -eo pipefail
              helm unittest helm/

              helm template data-proxy helm/ -f helm/ci/test-values.yaml \
                | nu --stdin -c '$in | from yaml | compact | each {|doc| match $doc.kind { "Cluster" => ($doc | upsert spec.instances 1), _ => $doc } | to json --raw } | str join "\n---\n"' \
                | kubeconform -strict -summary -ignore-missing-schemas -schema-location default -schema-location '${crdSchema}'
            '';
            execIfModified = [ "helm" ];
          };
          "dp:lint:docs" = {
            exec = ''
              vale sync --no-exit
              vale docs/ README.md STYLEGUIDE.md src/data_proxy/ scripts/
            '';
            execIfModified = [
              "docs"
              "README.md"
              "STYLEGUIDE.md"
              "src"
              "scripts"
              ".vale.ini"
              ".vale/styles"
            ];
          };
          "dp:lint".after = [
            "dp:lint:ci"
            "dp:lint:sql"
            "dp:lint:py"
            "dp:lint:helm"
            "dp:lint:proxy"
            "dp:lint:nu"
            "dp:lint:containers"
            "dp:lint:docs"
          ];
          "dp:test".after = [
            "dp:test:py"
            "dp:test:helm"
            "dp:test:proxy"
          ];
        };
      };
    };

    default = {
      extends = [ "quality" ];
      module = {
        env.KUBECONFIG = ".kubeconfig";

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

        scripts = {
          ci.exec = ''nu scripts/ci.nu "$@"'';
          cluster.exec = ''nu scripts/cluster.nu "$@"'';
          seed.exec = ''uv run python scripts/seed.py "$@"'';
          token.exec = "nu scripts/token.nu";
        };

        git-hooks.hooks = {
          ruff.enable = true;
          ruff-format.enable = true;
          ripsecrets.enable = true;
          basedpyright = {
            enable = true;
            name = "basedpyright";
            entry = "uv run basedpyright src/ tests/";
            language = "system";
            types = [ "python" ];
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
