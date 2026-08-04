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

  languages.python = {
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

  treefmt.config.programs.sqlfluff.enable = true;

  git-hooks.hooks = {
    ruff.enable = true;
    ruff-format.enable = true;
    ripsecrets.enable = true;
    ty = {
      enable = true;
      name = "ty";
      entry = "uv run ty check";
      language = "system";
      types = [ "python" ];
      pass_filenames = false;
    };
  };

  scripts = {
    up.exec = "docker-compose up -d";
    down.exec = "docker-compose down";
    reset.exec = "docker-compose down -v";
    logs.exec = "docker-compose logs -f";
  };

  tasks = {
    "app:test".exec = "pytest --cov=dp --cov-report=term-missing --ignore-glob='*constants*' --ignore-glob='*duckdb*' --ignore-glob='*settings*' --ignore-glob='*templates*' --ignore-glob='*models*'";
    "app:lint".exec = "ruff check && ruff format --check";
    "app:fmt".exec = "ruff check --fix && ruff format";
  };
}
