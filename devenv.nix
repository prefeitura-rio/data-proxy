{ pkgs, config, ... }:

{
  name = "data-proxy";

  env = {
    UV_PYTHON = config.languages.python.package.outPath;
  };

  packages = with pkgs; [
    curl
    jq
    k6
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
    seed.exec = ''uv run python scripts/seed.py "$@"'';
    get-token.exec = "bash scripts/token.sh";
  };

  tasks = {
    "app:test".exec = "uv run pytest --cov=dp --cov-report=term-missing";
    "app:lint".exec = "ruff check && basedpyright src/ tests/";
    "app:fmt".exec = "ruff check --fix && ruff format";
    "charts:lint".exec = "helm lint charts/*";
    "charts:test".exec = "helm unittest charts/*";
  };
}
