{ pkgs, ... }:
let
  bin = "./console/ui/node_modules/.bin";
  projects = "^console/(ui|notebooks)/";
in
{
  profiles.console = {
    extends = [
      "base"
      "python"
    ];
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
          description: dir: script: files:
          utils.nuTask {
            inherit description;
            cwd = dir;
            after = [ "console:install:${lib.last (lib.splitString "/" dir)}" ];
            exec = script;
            execIfModified = utils.mkInputs files;
          };
      in
      {
        imports = [
          ../nix/processes/keycloak.nix
          ../nix/processes/postgres.nix
          ../nix/processes/postgrest.nix
          ../nix/processes/seaweedfs.nix
          ../nix/processes/console.nix
        ];

        env = {
          CONSOLE_URL = utils.urls.console;
          NOTEBOOKS_URL = utils.urls.notebooks;
          KEYCLOAK_URL = utils.urls.keycloak;
          KEYCLOAK_USER_PASSWORD = "web-test";
          E2E_DATABASE_URL = utils.postgresUrl { database = "data_proxy"; };
          PLAYWRIGHT_BROWSERS_PATH = "${pkgs.playwright-driver.browsers}";
          PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS = "true";
          PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = "1";
        };

        languages = {
          javascript = {
            enable = true;
            pnpm.enable = true;
          };
          typescript.enable = true;
        };

        scripts.migrate = {
          description = "Run one goose command on the application database.";
          package = pkgs.nushell;
          binary = "nu";
          exec = ../scripts/migrate.nu;
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
          "console:install:notebooks" = installTask "console/notebooks";
          "lint:console" =
            pnpmTask "Check and lint the console app." "console/ui" "pnpm check\npnpm lint"
              consoleInputs;
          "lint:nb" = pnpmTask "Check and lint the notebooks." "console/notebooks" "pnpm check\npnpm lint" (
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
          "test:console" =
            pnpmTask "Run the unit tests of the console app." "console/ui" "pnpm test"
              consoleInputs;
        };

        enterTest = ''
          pnpm --dir console/ui exec bddgen
          pnpm --dir console/ui exec playwright test ''${PLAYWRIGHT_ARGS:-}
        '';
      };
  };
}
