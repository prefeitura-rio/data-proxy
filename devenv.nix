{ pkgs, config, ... }:
{
  name = "data-proxy";

  env = {
    UV_PYTHON = config.languages.python.package.outPath;
    UV_LINK_MODE = "copy";
    KUBECONFIG = ".kubeconfig";
    DOCKER_HOST = "unix:///run/user/1000/podman/podman.sock";
  };

  packages = with pkgs; [
    actionlint
    ast-grep
    http-nu
    k6
    kubeconform
    minikube
    nodejs
    nu-lint
    nushell
    seaweedfs
    sqlfluff
    minijinja
    typescript
    helmfile
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
    types.exec = "npm install --no-save @types/node @types/k6 njs-types >/dev/null";
  };

  tasks = {
    "dp:lint:ci".exec = "actionlint .github/workflows/*.yaml";
    "dp:lint:py".exec = ''
      set -e
      uv run ruff check src/ tests/
      uv run basedpyright src/ tests/
      uv run complexipy src/ tests/
      uv run vulture src/ tests/ --min-confidence 90
    '';
    "dp:lint:nu".exec = "nu-lint helm/files/*.nu";
    "dp:lint:sql".exec = ''
      sqlfluff lint --dialect postgres src/dp/sql/postgres helm/files/sql
      sqlfluff lint --dialect duckdb src/dp/sql/duckdb
      sqlfluff lint --dialect bigquery src/dp/sql/bigquery
    '';
    "dp:lint:helm".exec = ''
      helm lint helm/ -f helm/ci/test-values.yaml
      helm lint helm/ -f helm/ci/test-values-ha.yaml
      helmfile -f helmfile.yaml lint
    '';
    "dp:lint:proxy".exec = "tsc -p nginx";
    "dp:lint:k6".exec = "tsc -p k6 --noEmit";
    "dp:test:py".exec = "uv run pytest --cov=dp --cov-report=term-missing";
    "dp:test:py:mut".exec = "COVERAGE_CORE=ctrace uv run pytest --gremlins --gremlin-batch";
    "dp:test:proxy".exec =
      "node --experimental-config-file=nginx/node.config.json --test nginx/fallback.test.ts";
    "dp:test:helm".exec =
      let
        crdSchema = "https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json";
      in
      ''
        helm unittest helm/
        helm template data-proxy helm/ -f helm/ci/test-values.yaml | kubeconform -strict -summary -ignore-missing-schemas -schema-location default -schema-location '${crdSchema}'
        helm template data-proxy helm/ -f helm/ci/test-values-ha.yaml | kubeconform -strict -summary -ignore-missing-schemas -schema-location default -schema-location '${crdSchema}'
      '';
    "dp:lint".after = [
      "dp:lint:ci"
      "dp:lint:sql"
      "dp:lint:py"
      "dp:lint:helm"
      "dp:lint:proxy"
      "dp:lint:k6"
    ];
    "dp:test".after = [
      "dp:test:py"
      "dp:test:py:mut"
      "dp:test:helm"
      "dp:test:proxy"
    ];
  };
}
