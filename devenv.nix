{ pkgs, config, ... }:

{
  name = "data-proxy";

  env = {
    UV_PYTHON = config.languages.python.package.outPath;
  };

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

  packages = with pkgs; [
    curl
    jq
  ];

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
    no-commit-to-branch = {
      enable = true;
      settings.branch = [
        "master"
        "main"
      ];
    };
  };

  scripts = {
    up.exec = "docker-compose up -d";
    down.exec = "docker-compose down";
    reset.exec = "docker-compose down -v";
    logs.exec = "docker-compose logs -f";
  };

  tasks = {
    "app:lint".exec = "ruff check && ruff format --check";
    "app:fmt".exec = "ruff check --fix && ruff format";
  };
}
