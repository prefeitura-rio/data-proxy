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
      "release"
    ];
    module =
      { config, utils, ... }:
      let
        tasksWith =
          prefix:
          lib.filter (name: lib.hasPrefix prefix name && !(lib.hasSuffix ":all" name)) (
            lib.attrNames config.tasks
          );

        tasksOf =
          task: type:
          lib.filter (
            name:
            let
              parts = lib.splitString ":" name;
            in
            lib.length parts == 3
            && lib.elemAt parts 0 != "all"
            && lib.elemAt parts 1 == task
            && (type == null || lib.elemAt parts 2 == type)
            && !(lib.hasSuffix ":all" name)
          ) (lib.attrNames config.tasks);

        docsFiles = utils.mkGlobs "docs" [
          "md"
          "svg"
          "d2"
          "json"
        ];

      in
      {
        env = {
          D2_LAYOUT = "elk";
          D2_DARK_THEME = "200";
        };

        packages = with pkgs; [
          d2
          vale
        ];

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
          "ci:lint:workflows" = utils.nuTask {
            description = "Lint the GitHub workflows.";
            packages = with pkgs; [
              actionlint
              shellcheck
            ];
            exec = "actionlint .github/workflows/*.yaml";
            execIfModified = utils.mkInputs (
              utils.mkGlobs ".github/workflows" [ "yaml" ] ++ utils.mkGlobs ".github/actions" [ "yaml" ]
            );
          };
          "nu:lint:scripts" = utils.nuTask {
            description = "Lint the Nushell scripts.";
            packages = with pkgs; [ nu-lint ];
            exec = builtins.readFile ../../scripts/nu-lint.nu;
            execIfModified = utils.mkInputs [
              "helm/files/**/*.nu"
              "k3d/manifests/files/**/*.nu"
              "console/notebooks/**/*.nu"
              "scripts/**/*.nu"
              ".nu-lint.toml"
            ];
          };
          "nu:tests:unit" = utils.nuTask {
            description = "Run the Nushell tests.";
            exec = "nu helm/files/scripts/tests/run.nu helm/files/scripts/tests/lib.nu scripts/tests/lib.nu scripts/tests/docs.nu scripts/tests/keycloak.nu";
            execIfModified = utils.mkInputs [
              "helm/files/scripts/**/*.nu"
              "scripts/**/*.nu"
            ];
          };
          "chart:lint:toml" = utils.nuTask {
            description = "Lint the RBAC rules file.";
            packages = with pkgs; [ taplo ];
            exec = ''
              taplo lint helm/files/config/rbac.toml
              taplo fmt --check helm/files/config/rbac.toml
            '';
            execIfModified = utils.mkInputs [
              "helm/files/config/rbac.toml"
              "helm/files/config/rbac.schema.json"
              ".taplo.toml"
            ];
          };
          "containers:lint:files" = utils.nuTask {
            description = "Lint the Containerfiles.";
            packages = with pkgs; [ hadolint ];
            exec = lib.concatMapStringsSep "\n" (name: "hadolint ${name}") containerfiles;
            execIfModified = utils.mkInputs ([ ".hadolint.yaml" ] ++ containerfiles);
          };

          "docs:build:diagrams" = utils.nuTask {
            description = "Render the D2 diagrams to SVG.";
            exec = ''
              for file in (glob docs/diagrams/**/*.d2) {
                  d2 $file ($file | path parse | update extension svg | path join)
              }
            '';
            execIfModified = utils.mkInputs [ "docs/diagrams/**/*.d2" ];
          };
          "docs:build:all" = {
            description = "Generate the diagrams, the navigation and the role table.";
            after = tasksWith "docs:build:";
          };
          "docs:serve:site" = utils.nuTask {
            description = "Generate the diagrams, the navigation and the role table, then serve the docs.";
            packages =
              with pkgs;
              [
                lsof
                mkdocs
              ]
              ++ lib.optionals pkgs.stdenv.hostPlatform.isLinux [ pkgs.xdg-utils ];
            after = [ "docs:build:all" ];
            exec = "nu scripts/docs.nu serve --opener ${
              if pkgs.stdenv.hostPlatform.isDarwin then "open" else "xdg-open"
            }";
          };
          "docs:build:roles" = utils.nuTask {
            description = "Write the role and permission table in the Roles page.";
            exec = "nu scripts/docs.nu write";
            execIfModified = utils.mkInputs [
              "helm/files/config/rbac.toml"
              "scripts/docs.nu"
            ];
          };
          "docs:lint:roles" = utils.nuTask {
            description = "Check that the role and permission table in the Roles page is current.";
            exec = "nu scripts/docs.nu check";
            execIfModified = utils.mkInputs [
              "helm/files/config/rbac.toml"
              "docs/security/roles.md"
              "scripts/docs.nu"
            ];
          };
          "docs:build:nav" = utils.nuTask {
            description = "Write the navigation block of every docs page and the MkDocs navigation, in the order of docs/.nav.json.";
            exec = "nu scripts/docs.nu nav write";
            execIfModified = utils.mkInputs (
              [
                "scripts/docs.nu"
              ]
              ++ docsFiles
            );
          };
          "docs:lint:nav" = utils.nuTask {
            description = "Check the navigation blocks and docs/.nav.json against the docs files.";
            exec = "nu scripts/docs.nu nav check";
            execIfModified = utils.mkInputs (
              [
                "scripts/docs.nu"
              ]
              ++ docsFiles
            );
          };
          "docs:lint:links" = utils.nuTask {
            description = "Check that the links of the docs, the README and the style guide point to a file and a heading.";
            exec = "nu scripts/docs.nu links";
            execIfModified = utils.mkInputs (
              [
                "README.md"
                "STYLEGUIDE.md"
                "scripts/docs.nu"
              ]
              ++ docsFiles
            );
          };
          "docs:lint:diagrams" = utils.nuTask {
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
            execIfModified = utils.mkInputs (
              utils.mkGlobs "docs/diagrams" [
                "d2"
                "svg"
              ]
            );
          };

          "all:lint:all" = {
            description = "Run every lint task.";
            after = tasksOf "lint" null;
          };
          "all:tests:unit" = {
            description = "Run every test task that needs no service.";
            after = tasksOf "tests" "unit";
          };
        };
      };
  };
}
