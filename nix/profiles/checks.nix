{
  pkgs,
  ...
}:
{
  profiles.checks = {
    extends = [
      "sync"
      "console"
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
        taplo
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
        "lint:toml" = {
          exec = ''
            taplo lint helm/files/config/rbac.toml
            taplo fmt --check helm/files/config/rbac.toml
          '';
          execIfModified = [
            "helm/files/config/rbac.toml"
            "helm/files/config/rbac.schema.json"
            ".taplo.toml"
          ];
        };
        "lint:containers" = {
          exec = ''
            hadolint Containerfile.sync
            hadolint Containerfile.postgres
            hadolint Containerfile.jobs
            hadolint Containerfile.proxy
            hadolint Containerfile.k6
          '';
          execIfModified = [
            ".hadolint.yaml"
            "Containerfile.sync"
            "Containerfile.postgres"
            "Containerfile.jobs"
            "Containerfile.proxy"
            "Containerfile.k6"
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
            "sync/src/**/*.py"
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
        "lint:console"
        "lint:chart"
        "lint:proxy"
        "lint:nu"
        "lint:toml"
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
