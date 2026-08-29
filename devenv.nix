{ pkgs, config, ... }:

{
  name = "data-proxy";

  env = {
    UV_PYTHON = config.languages.python.package.outPath;
    KUBECONFIG = "$DEVENV_ROOT/.kubeconfig";
  };

  packages = with pkgs; [
    curl
    jq
    k6
    minikube
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
    "app:test".exec = "uv run pytest --cov=dp --cov-report=term-missing";
    "app:lint".exec = ''
      uv run ruff check src/ tests/
      uv run basedpyright src/ tests/
      uv run complexipy src/ tests/
      uv run vulture src/ tests/
    '';
    "app:fmt".exec = "ruff check --fix && ruff format";
    "charts:lint".exec = "helm lint helm/";
    "charts:test".exec = "helm unittest helm/";
  };
}
