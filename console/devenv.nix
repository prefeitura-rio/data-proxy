{ pkgs, ... }:
let
  bin = "./console/ui/node_modules/.bin";
  projects = "^console/(ui|notebooks)/";
in
{
  profiles.ui = {
    extends = [ "base" ];
    module =
      { lib, utils, ... }:
      let
        consoleInputs = [
          "console/ui/package.json"
          "console/ui/pnpm-lock.yaml"
          "console/ui/tsconfig.json"
          "console/ui/vite.config.ts"
          "console/ui/playwright.config.ts"
          "console/ui/eslint.config.js"
          "console/ui/.prettierrc.mjs"
          "console/ui/.npmrc"
          ".prettierignore"
        ]
        ++ utils.mkGlobs "console/ui/src" [
          "ts"
          "svelte"
          "svg"
          "html"
          "css"
        ]
        ++ utils.mkGlobs "console/ui/tests" [
          "ts"
          "feature"
        ];

        installTask =
          dir:
          utils.nuTask {
            description = "Install the locked dependencies of ${dir}.";
            cwd = dir;
            exec = "pnpm install --frozen-lockfile";
          };

        pnpmTask =
          description: dir: install: script: files:
          utils.nuTask {
            inherit description;
            cwd = dir;
            after = [ install ];
            exec = script;
            execIfModified = utils.mkInputs files;
          };
      in
      {
        languages = {
          javascript = {
            enable = true;
            pnpm.enable = true;
          };
          typescript.enable = true;
        };

        git-hooks.hooks = {
          prettier = {
            enable = true;
            files = projects;
            settings = {
              write = true;
              ignore-unknown = true;
              ignore-path = [ "./.prettierignore" ];
              binPath = "${bin}/prettier";
            };
          };
          eslint = {
            enable = true;
            settings = {
              binPath = "${bin}/eslint";
              extensions = "${projects}.*\\.(ts|js|mjs|svelte)$";
            };
          };
        };

        tasks = {
          "devenv:git-hooks:run".before = lib.mkForce [ ];
          "console:install:ui" = installTask "console/ui";
          "nb:install:deps" = installTask "console/notebooks";
          "console:lint:ui" =
            pnpmTask "Check and lint the console app." "console/ui" "console:install:ui" "pnpm check\npnpm lint"
              consoleInputs;
          "nb:lint:ts" =
            pnpmTask "Check and lint the notebooks." "console/notebooks" "nb:install:deps"
              "pnpm check\npnpm lint"
              (
                [
                  "console/notebooks/*.ts"
                  "console/notebooks/tsconfig.json"
                  "console/notebooks/package.json"
                  "console/notebooks/pnpm-lock.yaml"
                  "console/notebooks/pnpm-workspace.yaml"
                  "console/notebooks/eslint.config.js"
                  "console/notebooks/.prettierrc"
                  ".prettierignore"
                ]
                ++ utils.mkGlobs "console/notebooks/src" [ "ts" ]
              );
          "console:tests:unit" =
            pnpmTask "Run the unit tests of the console app." "console/ui" "console:install:ui" "pnpm test"
              consoleInputs;
        };

      };
  };

  profiles.console = {
    extends = [
      "ui"
      "python"
    ];
    module.imports = [ ./e2e.nix ];
  };

  profiles.nb = {
    extends = [ "console" ];
    module.imports = [ ../nix/processes/nb.nix ];
  };
}
