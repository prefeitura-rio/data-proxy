{
  pkgs,
  lib,
  ...
}:
let
  containerfiles = builtins.attrNames (
    lib.filterAttrs (name: _: lib.hasPrefix "Containerfile." name) (builtins.readDir ../..)
  );
in
{
  profiles.checks = {
    extends = [
      "sync"
      "console"
      "proxy"
      "chart"
    ];
    module =
      { config, utils, ... }:
      let
        tasksWith =
          prefix:
          lib.filter (name: lib.hasPrefix prefix name && !(lib.hasSuffix ":all" name)) (
            lib.attrNames config.tasks
          );
      in
      {
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
          description = "Compute the CI change flags and publish the chart and the images.";
          package = pkgs.nushell;
          binary = "nu";
          exec = ../../scripts/ci.nu;
        };

        git-hooks.hooks.ripsecrets.enable = true;

        tasks = {
          "lint:ci" = utils.nuTask {
            description = "Lint the GitHub workflows.";
            exec = "actionlint .github/workflows/*.yaml";
            execIfModified = [
              ".github/workflows"
              ".github/actions"
            ];
          };
          "lint:nu" = utils.nuTask {
            description = "Lint the Nushell scripts.";
            exec = builtins.readFile ../../scripts/nu-lint.nu;
            execIfModified = [
              "helm/files/**/*.nu"
              "k3d/manifests/files/**/*.nu"
              "console/notebooks/**/*.nu"
              "scripts/**/*.nu"
              ".nu-lint.toml"
            ];
          };
          "test:nu" = utils.nuTask {
            description = "Run the Nushell tests.";
            exec = "nu helm/files/scripts/tests/run.nu helm/files/scripts/tests/lib.nu scripts/tests/lib.nu";
            execIfModified = [
              "helm/files/scripts"
              "scripts"
            ];
          };
          "lint:toml" = utils.nuTask {
            description = "Lint the RBAC rules file.";
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
          "lint:containers" = utils.nuTask {
            description = "Lint the Containerfiles.";
            exec = lib.concatMapStringsSep "\n" (name: "hadolint ${name}") containerfiles;
            execIfModified = [ ".hadolint.yaml" ] ++ containerfiles;
          };
          "lint:docs" = utils.nuTask {
            description = "Lint the prose of the documentation.";
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

          "lint:all" = {
            description = "Run every lint task.";
            after = tasksWith "lint:";
          };
          "test:all" = {
            description = "Run every test task that needs no service.";
            after = tasksWith "test:";
          };
        };
      };
  };
}
