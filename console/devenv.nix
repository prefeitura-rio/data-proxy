{ ... }:
{
  profiles.console = {
    extends = [
      "base"
      "python"
    ];
    module =
      {
        config,
        data,
        lib,
        pkgs,
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
        postgresPort = data.port "pg" "main";
        appDatabase = data.databases.app;
        uv = [
          "uv"
          "run"
          "--frozen"
          "--project"
          "sync"
        ];
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
        setupEnv = {
          AUTH_ANON_ROLE = postgres.roles.anon;
          AUTH_SERVING_ROLE = postgres.roles.serving;
          AUTH_SIGNER_ROLE = postgres.roles.signer;
          AUTH_USER_ROLE = postgres.roles.user;
          CATALOG_PORT = postgresPort;
          DBOS_APPLICATION_NAME = "data-proxy-sync";
          DBOS_APP_SCHEMA = "data_proxy";
          DBOS_SYSTEM_DATABASE_URL = utils.postgresUrl { database = appDatabase; };
          DBOS_SYSTEM_SCHEMA = "dbos";
          DUCKDB_PASSWORD = postgres.passwords.duckdb;
          DUCKLAKE_ADMIN_URL = utils.postgresUrl { database = catalogDatabase; };
          DUCKLAKE_CATALOG_PATH = "ducklake";
          DUCKLAKE_CATALOG_URL = utils.postgresUrl { database = catalogDatabase; };
          PGDATABASE = appDatabase;
          PGHOST = "127.0.0.1";
          PGPASSWORD = postgres.password;
          PGPORT = postgresPort;
          PGUSER = postgres.user;
          PG_DATABASE_URL = utils.postgresUrl { database = appDatabase; };
          PROVISION_ALL = "true";
          RBAC_FILE = "${config.devenv.root}/helm/files/config/rbac.toml";
          S3_ACCESS_KEY = storage.admin.accessKey;
          S3_BOOTSTRAP_RETRY = "2sec";
          S3_BOOTSTRAP_TIMEOUT = "2min";
          S3_BUCKET = s3Bucket;
          S3_CORS_ORIGINS = urls.nb;
          S3_ENDPOINT = "127.0.0.1:${toString (data.port "s3" "main")}";
          S3_SECRET_KEY = storage.admin.secretKey;
          S3_USE_SSL = "false";
          SERVING_PASSWORD = postgres.passwords.serving;
          SIGNER_PASSWORD = postgres.passwords.signer;
          SQL_TEMPLATE_DIR = "${config.devenv.root}/helm/files/templates";
          SYNC_CONFIG_PATH =
            pkgs.runCommand "sync.json" { }
              "${pkgs.writers.writeNu "sync-json" ''
                open ${../k3d/values/data-proxy.yaml} | get sync.config | to json | save --raw $env.out
              ''}";
        };

        notebookInputs = [
          "console/notebooks/*.ts"
          "console/notebooks/tsconfig.json"
          "console/notebooks/package.json"
          "console/notebooks/pnpm-lock.yaml"
          "console/notebooks/pnpm-workspace.yaml"
          "console/notebooks/.oxlintrc.json"
          "console/notebooks/.oxfmtrc.json"
        ]
        ++ utils.mkGlobs "console/notebooks/src" [
          "css"
          "ts"
        ];

        consoleInputs = [
          "console/ui/package.json"
          "console/ui/pnpm-lock.yaml"
          "console/ui/tsconfig.json"
          "console/ui/vite.config.ts"
          "console/ui/playwright.config.ts"
          "console/ui/eslint.config.js"
          "console/ui/.oxlintrc.json"
          "console/ui/.oxfmtrc.json"
          "console/ui/.npmrc"
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
        key = "data-proxy-console";
        imports = [
          ../nix/services/keycloak.nix
          ../nix/services/postgres.nix
          ../nix/services/postgrest.nix
          ../nix/services/seaweedfs.nix
          {
            languages = {
              javascript = {
                enable = true;
                pnpm.enable = true;
                lsp.enable = lib.mkDefault false;
              };
              typescript = {
                enable = true;
                lsp.enable = lib.mkDefault false;
              };
            };

            git-hooks.hooks = {
              console-lint-ui = utils.mkHook {
                profile = "console";
                task = "console:lint:ui";
                files = "^console/ui/.*\\.(ts|js|mjs|svelte|json)$";
              };
              console-lint-fmt = utils.mkHook {
                profile = "console";
                task = "console:lint:fmt";
                files = "^console/ui/.*\\.(ts|js|mjs|svelte|css|json|md)$";
              };
              nb-lint-ts = utils.mkHook {
                profile = "console";
                task = "nb:lint:ts";
                files = "^console/notebooks/.*\\.(ts|js|mjs|json)$";
              };
              nb-lint-fmt = utils.mkHook {
                profile = "console";
                task = "nb:lint:fmt";
                files = "^console/notebooks/.*\\.(ts|js|mjs|css|json|md)$";
              };
            };

            tasks = {
              "console:install:ui" = installTask "console/ui";
              "nb:install:deps" = installTask "console/notebooks";
              "console:lint:ui" =
                pnpmTask "Check and lint the console app." "console/ui" "console:install:ui" "pnpm check\npnpm lint"
                  consoleInputs;
              "console:lint:fmt" =
                pnpmTask "Check the format of the console app." "console/ui" "console:install:ui"
                  "pnpm format:check"
                  consoleInputs;
              "nb:lint:ts" =
                pnpmTask "Check and lint the notebooks." "console/notebooks" "nb:install:deps"
                  "pnpm check\npnpm lint"
                  notebookInputs;
              "nb:lint:fmt" =
                pnpmTask "Check the format of the notebooks." "console/notebooks" "nb:install:deps"
                  "pnpm format:check"
                  notebookInputs;
            };
          }
          {
            files = utils.fileEntry "init.sql" {
              text = ''
                CREATE DATABASE ${appDatabase};
                CREATE DATABASE ${catalogDatabase};
                CREATE ROLE duckdb LOGIN PASSWORD '${postgres.passwords.duckdb}';
                ALTER DATABASE ${appDatabase} SET duckdb.unsafe_allow_execution_inside_functions = 'on';
                ALTER DATABASE ${appDatabase} SET duckdb.unsafe_allow_mixed_transactions = 'on';
              '';
            };

            tasks = build // {
              "console:setup:stack" = utils.nuTask {
                description = "Initialize console databases, storage, roles, migrations and test fixtures.";
                cwd = config.devenv.root;
                env = utils.toStrings setupEnv;
                packages = with pkgs; [ rclone ];
                after = [
                  "devenv:files"
                  (utils.processTask "pg")
                  (utils.processTask "s3")
                ];
                before = [
                  (utils.processTask "console")
                  (utils.processTask "pgrest")
                ];
                exec = ''
                  psql --no-psqlrc --set=ON_ERROR_STOP=1 --dbname=postgres --file=${utils.stateFile "init.sql"}
                  nu helm/files/scripts/ensure-s3-bucket.nu --create
                  nu helm/files/scripts/configure-db.nu roles
                  ${lib.concatStringsSep " " uv} dbos migrate --sys-db-url $env.DBOS_SYSTEM_DATABASE_URL --schema dbos
                  goose -dir migrations/console postgres $env.PG_DATABASE_URL up
                  goose -dir migrations/ducklake postgres $env.DUCKLAKE_ADMIN_URL up
                  nu helm/files/scripts/configure-db.nu rules
                  nu helm/files/scripts/provision.nu
                  ${lib.concatStringsSep " " uv} python sync/tests/seeds/console.py
                '';
              };
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
              control-worker =
                utils.mkProcess {
                  command = uv ++ [
                    "python"
                    "-m"
                    "sync.dbos.main"
                  ];
                  env = setupEnv // {
                    LISTEN_QUEUES = "control,schedules";
                  };
                }
                // {
                  cwd = config.devenv.root;
                  after = [ "console:setup:stack" ];
                  ready = utils.probes.command [
                    "psql"
                    (utils.postgresUrl { database = appDatabase; })
                    "--no-psqlrc"
                    "--quiet"
                    "--command"
                    "SELECT 1 / count(*) FROM dbos.queues WHERE name = 'control'"
                  ];
                };

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
          {
            tasks = lib.mkIf (!config.devenv.isTesting) {
              "nb:build:site" =
                utils.mkTask {
                  command = [
                    "nu"
                    "build.nu"
                    "notebooks/query.py"
                    "site"
                  ];
                }
                // {
                  description = "Build the notebook site.";
                  cwd = "${config.devenv.root}/console/notebooks";
                  before = [ (utils.processTask "nb") ];
                  execIfModified = utils.mkInputs (
                    [
                      "console/notebooks/build.nu"
                      "console/notebooks/bundle.ts"
                      "console/notebooks/tsconfig.json"
                      "console/notebooks/package.json"
                      "console/notebooks/pnpm-lock.yaml"
                      "console/notebooks/pnpm-workspace.yaml"
                      "console/notebooks/pyproject.toml"
                      "console/notebooks/uv.lock"
                    ]
                    ++ utils.mkGlobs "console/notebooks/src" [
                      "css"
                      "ts"
                    ]
                    ++ utils.mkGlobs "console/notebooks/notebooks" [ "py" ]
                  );
                };
            };

            processes = lib.mkIf (!config.devenv.isTesting) {
              nb =
                utils.mkProcess {
                  command = [
                    "node"
                    "server.ts"
                  ];
                  env = {
                    PORT = data.notebooksPort;
                    CONSOLE_ORIGIN = urls.console;
                    STORAGE_ORIGIN = urls.s3;
                  };
                }
                // {
                  cwd = "${config.devenv.root}/console/notebooks";
                  ready = utils.probes.http {
                    port = data.notebooksPort;
                    path = "/config.js";
                  };
                };
            };
          }
          {
            env = {
              CONSOLE_URL = data.urls.console;
              KEYCLOAK_URL = data.urls.kc;
              KEYCLOAK_USER_PASSWORD = "web-test";
              INTEGRATION_DATABASE_URL = utils.postgresUrl { database = "data_proxy"; };
              PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS = "true";
              PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = "1";
              PLAYWRIGHT_BROWSERS_PATH = "${pkgs.playwright-driver.browsers.override {
                withChromium = false;
                withFfmpeg = false;
                withFirefox = false;
                withWebkit = false;
              }}";
            };

            scripts.migrate = {
              description = "Run one goose command on the application database.";
              package = pkgs.nushell;
              binary = "nu";
              exec = ../scripts/migrate.nu;
            };

            enterTest = ''
                set -e
              pnpm --dir console/ui test
              pnpm --dir console/ui exec bddgen
                pnpm --dir console/ui exec playwright test ''${PLAYWRIGHT_ARGS:-}
            '';
          }
        ];
      };
  };

  profiles.model = {
    extends = [ "console" ];
    module =
      {
        lib,
        data,
        pkgs,
        utils,
        ...
      }:
      {
        packages = [ pkgs.tbls ];

        env = {
          TBLS_APP_DSN = "${utils.postgresUrl { database = data.databases.app; }}?sslmode=disable";
          TBLS_CATALOG_DSN = "${utils.postgresUrl { database = data.databases.catalog; }}?sslmode=disable";
        };

        enterTest = lib.mkForce "nu scripts/db.nu diagrams";
      };
  };
}
