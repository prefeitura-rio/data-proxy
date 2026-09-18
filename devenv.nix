{ pkgs, config, ... }:
{
  name = "data-proxy";

  env = {
    UV_PYTHON = config.languages.python.package.outPath;
    UV_LINK_MODE = "copy";
    KUBECONFIG = ".kubeconfig";
  };

  packages = with pkgs; [
    actionlint
    ast-grep
    gh
    git
    hadolint
    http-nu
    kubeconform
    minikube
    nodejs
    nu-lint
    nushell
    seaweedfs
    sqlfluff
    minijinja
    typescript
    yq-go
    kubecolor
    (google-cloud-sdk.withExtraComponents (
      with google-cloud-sdk.components; [ gke-gcloud-auth-plugin ]
    ))
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
      uv = {
        enable = true;
        sync = {
          enable = true;
          allGroups = true;
        };
      };
    };
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

  scripts = {
    cluster.exec = ''nu scripts/cluster.nu "$@"'';
    seed.exec = ''uv run python scripts/seed.py "$@"'';
    token.exec = "nu scripts/token.nu";
  };

  tasks =
    let
      crdSchema = "https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json";
    in
    {
      "dp:types" = {
        exec = "npm install --no-save @types/node njs-types >/dev/null";
        status = "test -d node_modules/@types/node && test -d node_modules/njs-types";
      };
      "dp:lint:ci" = {
        exec = "actionlint .github/workflows/*.yaml";
        execIfModified = [
          ".github/workflows"
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
        exec = "nu-lint helm/files/*.nu";
        execIfModified = [ "helm/files/**/*.nu" ];
      };
      "dp:lint:docker" = {
        exec = ''
          hadolint Dockerfile
          hadolint Dockerfile.postgres
          hadolint Dockerfile.nushell
          hadolint Dockerfile.proxy
        '';
        execIfModified = [
          "Dockerfile"
          "Dockerfile.postgres"
          "Dockerfile.nushell"
          "Dockerfile.proxy"
        ];
      };
      "dp:lint:sql" = {
        exec = ''
          sqlfluff lint --dialect postgres --config helm/files/sql/helm/.sqlfluff helm/files/sql/helm
          sqlfluff lint --dialect postgres src/dp/sql/postgres helm/files/sql/jinja
          sqlfluff lint --dialect duckdb src/dp/sql/duckdb
          sqlfluff lint --dialect bigquery src/dp/sql/bigquery
        '';
        execIfModified = [
          ".sqlfluff"
          "helm/files/sql/helm/.sqlfluff"
          "helm/files/sql"
          "src/dp/sql"
        ];
      };
      "dp:lint:helm" = {
        exec = ''
          helm lint helm/ -f helm/ci/test-values.yaml
          helm lint helm/ -f helm/ci/test-values-ha.yaml
        '';
        execIfModified = [ "helm" ];
      };
      "dp:lint:proxy" = {
        exec = "tsc -p nginx";
        after = [ "dp:types" ];
        execIfModified = [
          "nginx"
          "package.json"
          "package-lock.json"
        ];
      };
      "dp:test:py" = {
        exec = "uv run pytest --cov=dp --cov-report=term-missing";
        execIfModified = [
          "src"
          "tests"
          "pyproject.toml"
          "uv.lock"
        ];
      };
      "dp:test:proxy" = {
        exec = "node --experimental-config-file=nginx/node.config.json --test nginx/fallback.test.ts";
        execIfModified = [ "nginx" ];
      };
      "dp:test:helm" = {
        exec = ''
          helm unittest helm/
          helm template data-proxy helm/ -f helm/ci/test-values.yaml | kubeconform -strict -summary -ignore-missing-schemas -schema-location default -schema-location '${crdSchema}'
          helm template data-proxy helm/ -f helm/ci/test-values-ha.yaml | kubeconform -strict -summary -ignore-missing-schemas -schema-location default -schema-location '${crdSchema}'
        '';
        execIfModified = [ "helm" ];
      };
      "dp:lint".after = [
        "dp:lint:ci"
        "dp:lint:sql"
        "dp:lint:py"
        "dp:lint:helm"
        "dp:lint:proxy"
        "dp:lint:nu"
        "dp:lint:docker"
      ];
      "dp:test".after = [
        "dp:test:py"
        "dp:test:helm"
        "dp:test:proxy"
      ];
    };
}
