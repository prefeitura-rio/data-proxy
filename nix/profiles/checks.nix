{
  pkgs,
  lib,
  ...
}:
let
  opener = if pkgs.stdenv.hostPlatform.isDarwin then "open" else "xdg-open";
  openerPackages = lib.optionals pkgs.stdenv.hostPlatform.isLinux (with pkgs; [ xdg-utils ]);
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
          cocogitto
          d2
          vale
        ];

        env = {
          D2_LAYOUT = "elk";
          D2_DARK_THEME = "200";
        };

        scripts.ci = {
          description = "Compute the CI change flags and publish the chart and the images.";
          package = pkgs.nushell;
          binary = "nu";
          exec = ../../scripts/ci.nu;
          packages = with pkgs; [
            git
            go-containerregistry
          ];
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
            packages = with pkgs; [
              actionlint
              shellcheck
            ];
            exec = "actionlint .github/workflows/*.yaml";
            execIfModified = [
              ".github/workflows"
              ".github/actions"
            ];
          };
          "lint:nu" = utils.nuTask {
            description = "Lint the Nushell scripts.";
            packages = with pkgs; [ nu-lint ];
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
            packages = with pkgs; [ taplo ];
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
            packages = with pkgs; [ hadolint ];
            exec = lib.concatMapStringsSep "\n" (name: "hadolint ${name}") containerfiles;
            execIfModified = [ ".hadolint.yaml" ] ++ containerfiles;
          };

          "docs:build:diagrams" = utils.nuTask {
            description = "Render the D2 diagrams to SVG.";
            exec = ''
              for file in (glob docs/diagrams/**/*.d2) {
                  d2 $file ($file | path parse | update extension svg | path join)
              }
            '';
            execIfModified = [ "docs/diagrams/**/*.d2" ];
          };
          "docs:build:all" = {
            description = "Generate the diagrams, the navigation and the role table.";
            after = tasksWith "docs:build:";
          };
          "docs:serve" = utils.nuTask {
            description = "Generate the diagrams, the navigation and the role table, then serve the docs.";
            packages =
              with pkgs;
              [
                lsof
                mkdocs
              ]
              ++ openerPackages;
            after = [ "docs:build:all" ];
            exec = "nu scripts/docs.nu serve --opener ${opener}";
          };
          "docs:build:roles" = utils.nuTask {
            description = "Write the role and permission table in the Roles page.";
            exec = "nu scripts/docs.nu write";
            execIfModified = [
              "helm/files/config/rbac.toml"
              "scripts/docs.nu"
            ];
          };
          "lint:roles" = utils.nuTask {
            description = "Check that the role and permission table in the Roles page is current.";
            exec = "nu scripts/docs.nu check";
            execIfModified = [
              "helm/files/config/rbac.toml"
              "docs/security/roles.md"
              "scripts/docs.nu"
            ];
          };
          "docs:build:nav" = utils.nuTask {
            description = "Write the navigation block of every docs page and the MkDocs navigation, in the order of docs/.nav.json.";
            exec = "nu scripts/docs.nu nav write";
            execIfModified = [
              "docs"
              "mkdocs.nav.yml"
              "scripts/docs.nu"
            ];
          };
          "lint:nav" = utils.nuTask {
            description = "Check the navigation blocks and docs/.nav.json against the docs files.";
            exec = "nu scripts/docs.nu nav check";
            execIfModified = [
              "docs"
              "scripts/docs.nu"
            ];
          };
          "lint:diagrams" = utils.nuTask {
            description = "Check that the rendered diagrams are current.";
            exec = ''
              for file in (glob docs/diagrams/**/*.d2) {
                  let svg = $file | path parse | update extension svg | path join
                  let fresh = d2 $file - | complete

                  if $fresh.exit_code != 0 {
                      error make { msg: $fresh.stderr }
                  }

                  if ($fresh.stdout | str trim) != (open --raw $svg | str trim) {
                      error make { msg: $"($svg) is out of date: run docs:build:diagrams" }
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
