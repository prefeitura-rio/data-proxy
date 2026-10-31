{ pkgs, utils, ... }:
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
    KEYCLOAK_URL = utils.urls.keycloak;
    KEYCLOAK_USER_PASSWORD = "web-test";
    E2E_DATABASE_URL = utils.postgresUrl { database = "data_proxy"; };
    PLAYWRIGHT_BROWSERS_PATH = "${pkgs.playwright-driver.browsers.override {
      withFirefox = false;
      withWebkit = false;
    }}";
    PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS = "true";
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = "1";
  };

  scripts.migrate = {
    description = "Run one goose command on the application database.";
    package = pkgs.nushell;
    binary = "nu";
    exec = ../scripts/migrate.nu;
  };

  enterTest = ''
    pnpm --dir console/ui exec bddgen
    pnpm --dir console/ui exec playwright test ''${PLAYWRIGHT_ARGS:-}
  '';
}
