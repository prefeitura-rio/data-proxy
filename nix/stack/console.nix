{
  lib,
  pkgs,
  config,
  data,
  utils,
  ...
}:
let
  inherit (data.credentials) postgres storage;
  inherit (data) urls;
  appPort = data.port "console" "main";
  gatewayPort = data.port "gateway" "main";
  catalogDatabase = data.databases.catalog;
  s3Bucket = data.bucket;

  consoleEnv = {
    COOKIE_SECRET = "test-cookie-secret-test-cookie-secret";
    GRANT_TTL_SECONDS = "30";
    NOTEBOOK_ORIGIN = urls.nb;
    OIDC_CLIENT_ID = "data-proxy-console-web";
    OIDC_CLIENT_SECRET = "test-secret";
    OIDC_ISSUER = urls.issuer;
    ORIGIN = urls.console;
    PORT = appPort;
    POSTGREST_URL = urls.pgrest;
    PROTOCOL_HEADER = "x-forwarded-proto";
    S3_ACCESS_KEY = storage.reader.accessKey;
    S3_ADMIN_ACCESS_KEY = storage.admin.accessKey;
    S3_ADMIN_SECRET_KEY = storage.admin.secretKey;
    S3_BUCKET = s3Bucket;
    S3_ENDPOINT = urls.s3;
    S3_REGION = "us-east-1";
    S3_SECRET_KEY = storage.reader.secretKey;
    CATALOG_URL = utils.postgresUrl {
      database = catalogDatabase;
      user = postgres.roles.signer;
      password = postgres.passwords.signer;
    };
  };

  build = {
    "console:build:ui" =
      utils.mkTask {
        command = [
          "pnpm"
          "build"
        ];
        env = consoleEnv;
      }
      // {
        description = "Build the console server.";
        cwd = "${config.devenv.root}/console/ui";
        after = [ "console:install:ui" ];
        before = [ (utils.processTask "console") ];
        execIfModified = utils.mkInputs (
          [
            "console/ui/package.json"
            "console/ui/pnpm-lock.yaml"
            "console/ui/.npmrc"
            "console/ui/tsconfig.json"
            "console/ui/vite.config.ts"
          ]
          ++ utils.mkGlobs "console/ui/src" [
            "ts"
            "svelte"
            "svg"
            "html"
            "css"
          ]
          ++ utils.mkGlobs "console/ui/static" [ "txt" ]
        );
      };
  };
in
{
  tasks = build // {
    "console:open:browser" = lib.mkIf (!config.devenv.isTesting) (
      utils.mkTask {
        command = [
          (if pkgs.stdenv.hostPlatform.isDarwin then "open" else "xdg-open")
          "${urls.console}/console"
        ];
        packages = lib.optionals pkgs.stdenv.hostPlatform.isLinux (with pkgs; [ xdg-utils ]);
      }
      // {
        description = "Open the console in the browser. Run it with `devenv tasks run console:open:browser`.";
        after = [ (utils.processTask "gateway") ];
      }
    );
  };

  processes = {
    console =
      utils.mkProcess {
        command = [
          "node"
          "build"
        ];
        env = consoleEnv;
      }
      // {
        cwd = "${config.devenv.root}/console/ui";
        ports.main.allocate = 13004;
        after = map utils.processTask [
          "pg"
          "s3"
          "kc"
          "pgrest"
        ];
        ready = utils.probes.http {
          port = appPort;
          path = "/console/healthz";
        };
      };

    gateway =
      utils.mkProcess {
        command = [
          (lib.getExe pkgs.caddy)
          "reverse-proxy"
          "--from"
          ":${toString gatewayPort}"
          "--to"
          "127.0.0.1:${toString appPort}"
        ];
      }
      // {
        ports.main.allocate = 13001;
        ready = utils.probes.http {
          port = gatewayPort;
          path = "/console/healthz";
        };
        after = [ (utils.processTask "console") ];
      };
  };
}
