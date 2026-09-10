{ pkgs, config, ... }:
{
  name = "data-proxy";

  env = {
    UV_PYTHON = config.languages.python.package.outPath;
    KUBECONFIG = ".kubeconfig";
    DOCKER_HOST = "unix:///run/user/1000/podman/podman.sock";
    NU_LIB_DIRS = "vendor";
  };

  packages = with pkgs; [
    ast-grep
    http-nu
    k6
    kubeconform
    minikube
    nu-lint
    nushell
    typescript
    nodejs
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
      entry = "${pkgs.uv}/bin/uv run basedpyright src/ tests/";
      language = "system";
      types = [ "python" ];
      pass_filenames = false;
    };
  };

  scripts = {
    seed.exec = ''${pkgs.uv}/bin/uv run python scripts/seed.py "$@"'';
    token.exec = "${pkgs.nushell}/bin/nu scripts/token.nu";
    cluster.exec = ''${pkgs.nushell}/bin/nu scripts/cluster.nu "$@"'';
    nginx-ts-types.exec = ''
      rm -rf nginx/types nginx/njs.d.ts
      ln -s ${pkgs.nginxModules.njs}/ts nginx/types
    '';
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
    "dp:tsc".exec = "${pkgs.typescript}/bin/tsc -p nginx";
    "dp:build:proxy".exec = "${pkgs.typescript}/bin/tsc -p nginx --noEmit false --outDir nginx/build";
    "dp:test:proxy".exec =
      "${pkgs.nodejs}/bin/node --experimental-config-file=nginx/node.config.json --test nginx/fallback.test.ts";
    "charts:lint".exec = "helm lint helm/";
    "charts:test".exec = "${pkgs.nushell}/bin/nu scripts/test-charts.nu";
  };

  enterShell = "nginx-ts-types";
}
