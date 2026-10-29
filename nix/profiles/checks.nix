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

        docsFiles = utils.filesOf "docs" [
          "md"
          "svg"
          "d2"
          "json"
        ];

        # The inputs of the suites that start services. A suite runs again only when one of its inputs changes. Every input
        # is a file or a glob over files, and `filesOf` names the extensions of a folder and throws for another one.
        environment = [
          "devenv.nix"
          "devenv.lock"
          "devenv.yaml"
          "nix/utils.nix"
          "nix/data.nix"
          "nix/profiles/*.nix"
        ];

        postgresServer = [
          "nix/processes/postgres.nix"
          "Containerfile.postgres"
        ];

        # What the sync code is made of, and what its tests read.
        syncSources = [
          "sync/pyproject.toml"
          "sync/uv.lock"
          "sync/src/**/*.py"
          "sync/src/**/*.sql"
        ];

        proxyInputs =
          [
            "proxy/devenv.nix"
            "proxy/lux.toml"
            "proxy/lux.lock"
            "proxy/src/**/*.lua"
            "scripts/lux.nu"
            "nix/processes/proxy.nix"
            "nix/services/redis.nix"
          ]
          ++ utils.filesOf "proxy/tests/integration" [
            "hurl"
            "json"
            "conf"
          ]
          ++ environment;

        syncInputs =
          [
            "sync/devenv.nix"
            "sync/tests/**/*.py"
            "sync/tests/**/*.sql"
            "sync/tests/**/*.feature"
            "console/migrations/*.sql"
            "scripts/db.nu"
            "nix/processes/sync.nix"
            "nix/processes/seaweedfs.nix"
          ]
          ++ utils.filesOf "sync/config" [
            "toml"
            "json"
          ]
          ++ utils.filesOf "sync/tests/files" [
            "parquet"
            "csv"
          ]
          ++ utils.filesOf "helm/files/templates" [ "sql" ]
          ++ utils.filesOf "helm/files/config" [
            "toml"
            "json"
            "conf"
          ]
          ++ utils.filesOf "proxy/tests/unit/fixtures" [ "json" ]
          ++ syncSources
          ++ postgresServer
          ++ environment;

        # The console tests seed their data with the sync code and the helm templates, and they read the values of k3d.
        consoleInputs =
          [
            "console/devenv.nix"
            "console/migrations/*.sql"
            "console/ui/package.json"
            "console/ui/pnpm-lock.yaml"
            "console/ui/pnpm-workspace.yaml"
            "console/ui/tsconfig.json"
            "console/ui/vite.config.ts"
            "console/ui/playwright.config.ts"
            "console/ui/.npmrc"
            "console/notebooks/*.ts"
            "console/notebooks/build.nu"
            "console/notebooks/package.json"
            "console/notebooks/pnpm-lock.yaml"
            "console/notebooks/pnpm-workspace.yaml"
            "console/notebooks/pyproject.toml"
            "console/notebooks/uv.lock"
            "sync/tests/*.py"
            "k3d/values/*.yaml"
            "scripts/db.nu"
            "scripts/migrate.nu"
            "nix/processes/console.nix"
            "nix/processes/keycloak.nix"
            "nix/processes/postgrest.nix"
            "nix/processes/seaweedfs.nix"
          ]
          ++ utils.filesOf "console/ui/src" [
            "ts"
            "svelte"
            "svg"
            "html"
            "css"
          ]
          ++ utils.filesOf "console/ui/static" [ "txt" ]
          ++ utils.filesOf "console/ui/tests" [
            "ts"
            "feature"
          ]
          ++ utils.filesOf "console/notebooks/src" [ "ts" ]
          ++ utils.filesOf "console/notebooks/notebooks" [ "py" ]
          ++ utils.filesOf "sync/tests/seeds" [ "py" ]
          ++ utils.filesOf "sync/tests/fixtures" [ "py" ]
          ++ utils.filesOf "sync/tests/files" [
            "parquet"
            "csv"
          ]
          ++ utils.filesOf "sync/tests/templates" [ "sql" ]
          ++ utils.filesOf "helm/files" [
            "sql"
            "nu"
            "toml"
            "json"
            "conf"
          ]
          ++ syncSources
          ++ postgresServer
          ++ environment;
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
            execIfModified = utils.inputsOf (
              utils.filesOf ".github/workflows" [ "yaml" ] ++ utils.filesOf ".github/actions" [ "yaml" ]
            );
          };
          "lint:nu" = utils.nuTask {
            description = "Lint the Nushell scripts.";
            packages = with pkgs; [ nu-lint ];
            exec = builtins.readFile ../../scripts/nu-lint.nu;
            execIfModified = utils.inputsOf [
              "helm/files/**/*.nu"
              "k3d/manifests/files/**/*.nu"
              "console/notebooks/**/*.nu"
              "scripts/**/*.nu"
              ".nu-lint.toml"
            ];
          };
          "test:nu" = utils.nuTask {
            description = "Run the Nushell tests.";
            exec = "nu helm/files/scripts/tests/run.nu helm/files/scripts/tests/lib.nu scripts/tests/lib.nu scripts/tests/docs.nu scripts/tests/keycloak.nu";
            execIfModified = utils.inputsOf [
              "helm/files/scripts/**/*.nu"
              "scripts/**/*.nu"
            ];
          };
          "lint:toml" = utils.nuTask {
            description = "Lint the RBAC rules file.";
            packages = with pkgs; [ taplo ];
            exec = ''
              taplo lint helm/files/config/rbac.toml
              taplo fmt --check helm/files/config/rbac.toml
            '';
            execIfModified = utils.inputsOf [
              "helm/files/config/rbac.toml"
              "helm/files/config/rbac.schema.json"
              ".taplo.toml"
            ];
          };
          "lint:containers" = utils.nuTask {
            description = "Lint the Containerfiles.";
            packages = with pkgs; [ hadolint ];
            exec = lib.concatMapStringsSep "\n" (name: "hadolint ${name}") containerfiles;
            execIfModified = utils.inputsOf ([ ".hadolint.yaml" ] ++ containerfiles);
          };

          "docs:build:diagrams" = utils.nuTask {
            description = "Render the D2 diagrams to SVG.";
            exec = ''
              for file in (glob docs/diagrams/**/*.d2) {
                  d2 $file ($file | path parse | update extension svg | path join)
              }
            '';
            execIfModified = utils.inputsOf [ "docs/diagrams/**/*.d2" ];
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
            execIfModified = utils.inputsOf [
              "helm/files/config/rbac.toml"
              "scripts/docs.nu"
            ];
          };
          "lint:roles" = utils.nuTask {
            description = "Check that the role and permission table in the Roles page is current.";
            exec = "nu scripts/docs.nu check";
            execIfModified = utils.inputsOf [
              "helm/files/config/rbac.toml"
              "docs/security/roles.md"
              "scripts/docs.nu"
            ];
          };
          "docs:build:nav" = utils.nuTask {
            description = "Write the navigation block of every docs page and the MkDocs navigation, in the order of docs/.nav.json.";
            exec = "nu scripts/docs.nu nav write";
            execIfModified = utils.inputsOf (
              [
              "scripts/docs.nu"
                        ] ++ docsFiles
            );
          };
          "lint:nav" = utils.nuTask {
            description = "Check the navigation blocks and docs/.nav.json against the docs files.";
            exec = "nu scripts/docs.nu nav check";
            execIfModified = utils.inputsOf (
              [
              "scripts/docs.nu"
                        ] ++ docsFiles
            );
          };
          "lint:links" = utils.nuTask {
            description = "Check that the links of the docs, the README and the style guide point to a file and a heading.";
            exec = "nu scripts/docs.nu links";
            execIfModified = utils.inputsOf (
              [
              "README.md"
              "STYLEGUIDE.md"
              "scripts/docs.nu"
                        ] ++ docsFiles
            );
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
            execIfModified = utils.inputsOf (utils.filesOf "docs/diagrams" [ "d2" "svg" ]);
          };

          "itest:proxy" = utils.serviceSuite {
            description = "Run the integration tests of the proxy against its test servers.";
            profile = "proxy";
            inputs = proxyInputs;
          };
          "itest:sync" = utils.serviceSuite {
            description = "Run the sync tests that need a database and a storage server.";
            profile = "sync";
            inputs = syncInputs;
          };
          "e2e:console" = utils.serviceSuite {
            description = "Run the browser tests of the console against its test servers.";
            profile = "console";
            inputs = consoleInputs;
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
