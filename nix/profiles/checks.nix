{
  pkgs,
  ...
}:
{
  profiles.checks = {
    extends = [
      "sync"
      "proxy"
      "chart"
    ];
    module = {
      packages = with pkgs; [
        actionlint
        hadolint
        nu-lint
        nushell
        shellcheck
        vale
      ];

      scripts.ci = {
        package = pkgs.nushell;
        binary = "nu";
        exec = ../../scripts/ci.nu;
      };

      git-hooks.hooks.ripsecrets.enable = true;

      tasks = {
        "lint:ci" = {
          exec = "actionlint .github/workflows/*.yaml";
          execIfModified = [
            ".github/workflows"
            ".github/actions"
          ];
        };
        "lint:nu" = {
          package = pkgs.nushell;
          exec = builtins.readFile ../../scripts/nu-lint.nu;
          execIfModified = [
            "helm/files/**/*.nu"
            "scripts/**/*.nu"
            ".nu-lint.toml"
          ];
        };
        "lint:containers" = {
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
        "lint:docs" = {
          exec = ''
            vale sync --no-exit
            vale docs/ README.md STYLEGUIDE.md sync/src/sync/ scripts/
          '';
          execIfModified = [
            "docs"
            "README.md"
            "STYLEGUIDE.md"
            "sync/*/src/**/*.py"
            "scripts"
            ".vale.ini"
            ".vale/styles"
          ];
        };
      };

      tasks."lint:all".after = [
        "lint:ci"
        "lint:sql"
        "lint:sync"
        "lint:chart"
        "lint:proxy"
        "lint:nu"
        "lint:containers"
        "lint:docs"
      ];
      tasks."test:all".after = [
        "test:chart"
        "test:proxy"
        "test:sync"
      ];
    };
  };
}
