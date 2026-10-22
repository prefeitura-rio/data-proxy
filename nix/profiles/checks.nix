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
          cocogitto
          d2
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

        git-hooks.hooks.vale = {
          enable = true;
          name = "vale";
          entry = toString (
            pkgs.writeShellScript "vale-hook" ''
              ${lib.getExe pkgs.vale} sync --no-exit
              exec ${lib.getExe pkgs.vale} "$@"
            ''
          );
          files = "^(docs/.*\\.md|README\\.md|STYLEGUIDE\\.md|sync/src/sync/.*\\.py|scripts/.*\\.nu)$";
        };

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

          "docs:diagrams" = utils.nuTask {
            description = "Render the D2 diagrams to SVG.";
            exec = ''
              for file in (glob docs/diagrams/**/*.d2) {
                  d2 --layout elk --dark-theme 200 $file ($file | str replace --regex '\.d2$' '.svg')
              }
            '';
            execIfModified = [ "docs/diagrams/**/*.d2" ];
          };
          "docs:roles" = utils.nuTask {
            description = "Write the role and permission table in the Roles page.";
            exec = "nu scripts/docs.nu write";
            execIfModified = [ "helm/files/config/rbac.toml" "scripts/docs.nu" ];
          };
          "lint:roles" = utils.nuTask {
            description = "Check that the role and permission table in the Roles page is current.";
            exec = "nu scripts/docs.nu check";
            execIfModified = [ "helm/files/config/rbac.toml" "docs/security/roles.md" "scripts/docs.nu" ];
          };
          "docs:nav" = utils.nuTask {
            description = "Write the navigation block of every docs page, in the order of docs/.nav.json.";
            exec = "nu scripts/docs.nu nav write";
            execIfModified = [ "docs" "scripts/docs.nu" ];
          };
          "lint:nav" = utils.nuTask {
            description = "Check the navigation blocks and docs/.nav.json against the docs files.";
            exec = "nu scripts/docs.nu nav check";
            execIfModified = [ "docs" "scripts/docs.nu" ];
          };
          "lint:diagrams" = utils.nuTask {
            description = "Check that the rendered diagrams are current.";
            exec = ''
              let temp = mktemp --directory
              for file in (glob docs/diagrams/**/*.d2) {
                  let svg = $file | str replace --regex '\.d2$' '.svg'
                  let fresh = $temp | path join ($svg | path basename)
                  d2 --layout elk --dark-theme 200 $file $fresh
                  if (open --raw $fresh) != (open --raw $svg) {
                      error make { msg: $"($svg) is out of date: run docs:diagrams" }
                  }
              }
            '';
            execIfModified = [ "docs/diagrams" ];
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
