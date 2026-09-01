{ pkgs, config, ... }:
{
  name = "data-proxy";

  env = {
    UV_PYTHON = config.languages.python.package.outPath;
    KUBECONFIG = "$DEVENV_ROOT/.kubeconfig";
    DOCKER_HOST = "unix:///run/user/1000/podman/podman.sock";
    TESTCONTAINERS_RYUK_DISABLED = "true";
  };

  packages = with pkgs; [
    curl
    jq
    k6
    minikube
    ast-grep
    kubeconform
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

  treefmt.config.programs.sqlfluff.enable = true;

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
    seed-data.exec = ''uv run python scripts/seed.py "$@"'';
    get-token.exec = "bash scripts/token.sh";
    cluster.exec = "bash scripts/cluster.sh \"$@\"";
  };

  tasks = {
    "dp:test".exec = "uv run pytest --cov=dp --cov-report=term-missing";
    "dp:test:mut".exec = "COVERAGE_CORE=ctrace uv run pytest --gremlins --gremlin-batch";
    "dp:lint".exec = ''
      uv run ruff check src/ tests/
      uv run basedpyright src/ tests/
      uv run complexipy src/ tests/
      uv run vulture src/ tests/
    '';
    "dp:fmt".exec = "ruff check --fix && ruff format";
    "charts:lint".exec = "helm lint helm/";
    "charts:test".exec = ''
      helm lint helm/ -f helm/ci/test-values.yaml
      helm lint helm/ -f helm/ci/test-values-ha.yaml
      helm unittest helm/

      for values in helm/ci/test-values.yaml helm/ci/test-values-ha.yaml; do
          helm template data-proxy helm/ -f "$values" | kubeconform -strict -summary -ignore-missing-schemas \
            -schema-location default \
            -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
      done
    '';
  };
}
