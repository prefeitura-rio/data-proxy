{ ... }:
{
  profiles.suites = {
    extends = [ "base" ];
    module =
      { utils, ... }:
      let
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

        syncSources = [
          "sync/pyproject.toml"
          "sync/uv.lock"
          "sync/src/**/*.py"
          "sync/src/**/*.sql"
        ];
      in
      {
        tasks = {
          "test:int:proxy" = utils.serviceSuite {
            description = "Run the integration tests of the proxy against its test servers.";
            profile = "proxy";
            inputs = [
              "proxy/devenv.nix"
              "proxy/lux.toml"
              "proxy/lux.lock"
              "proxy/src/**/*.lua"
              "scripts/lux.nu"
              "nix/processes/proxy.nix"
              "nix/services/redis.nix"
            ]
            ++ utils.mkGlobs "proxy/tests/integration" [
              "hurl"
              "json"
              "conf"
            ]
            ++ environment;
          };
          "test:int:sync" = utils.serviceSuite {
            description = "Run the sync tests that need a database and a storage server.";
            profile = "sync";
            inputs = [
              "sync/devenv.nix"
              "sync/tests/**/*.py"
              "sync/tests/**/*.sql"
              "sync/tests/**/*.feature"
              "console/migrations/*.sql"
              "scripts/db.nu"
              "nix/processes/sync.nix"
              "nix/processes/seaweedfs.nix"
            ]
            ++ utils.mkGlobs "sync/config" [
              "toml"
              "json"
            ]
            ++ utils.mkGlobs "sync/tests/files" [
              "parquet"
              "csv"
            ]
            ++ utils.mkGlobs "helm/files/templates" [ "sql" ]
            ++ utils.mkGlobs "helm/files/config" [
              "toml"
              "json"
              "conf"
            ]
            ++ utils.mkGlobs "proxy/tests/unit/fixtures" [ "json" ]
            ++ syncSources
            ++ postgresServer
            ++ environment;
          };
          "test:int:console" = utils.serviceSuite {
            description = "Run the browser tests of the console against its test servers.";
            profile = "console";
            inputs = [
              "console/devenv.nix"
              "console/migrations/*.sql"
              "console/ui/package.json"
              "console/ui/pnpm-lock.yaml"
              "console/ui/pnpm-workspace.yaml"
              "console/ui/tsconfig.json"
              "console/ui/vite.config.ts"
              "console/ui/playwright.config.ts"
              "console/ui/.npmrc"
              "sync/tests/*.py"
              "k3d/values/*.yaml"
              "scripts/db.nu"
              "scripts/migrate.nu"
              "nix/processes/console.nix"
              "nix/processes/keycloak.nix"
              "nix/processes/postgrest.nix"
              "nix/processes/seaweedfs.nix"
            ]
            ++ utils.mkGlobs "console/ui/src" [
              "ts"
              "svelte"
              "svg"
              "html"
              "css"
            ]
            ++ utils.mkGlobs "console/ui/static" [ "txt" ]
            ++ utils.mkGlobs "console/ui/tests" [
              "ts"
              "feature"
            ]
            ++ utils.mkGlobs "sync/tests/seeds" [ "py" ]
            ++ utils.mkGlobs "sync/tests/fixtures" [ "py" ]
            ++ utils.mkGlobs "sync/tests/files" [
              "parquet"
              "csv"
            ]
            ++ utils.mkGlobs "sync/tests/templates" [ "sql" ]
            ++ utils.mkGlobs "helm/files" [
              "sql"
              "nu"
              "toml"
              "json"
              "conf"
            ]
            ++ syncSources
            ++ postgresServer
            ++ environment;
          };
        };
      };
  };
}
