{
  pkgs,
  config,
  utils,
  ...
}:
let
  inherit (utils.credentials) postgres storage;
  inherit (utils) urls;
  appPort = utils.port "console" "main";
  gatewayPort = utils.port "gateway" "main";
  postgresPort = utils.port "postgres" "main";
  appDatabase = "data_proxy";
  catalogDatabase = "ducklake";
  s3Bucket = "test-bucket";
  uv = [
    "uv"
    "run"
    "--frozen"
    "--project"
    "sync"
  ];

  syncConfig =
    pkgs.runCommand "sync.json" { }
      "${pkgs.writers.writeNu "sync-json" ''
        open ${../../k3d/values/data-proxy.yaml} | get sync.config | to json | save --raw $env.out
      ''}";

  notebookInputs = [
    "console/notebooks/src"
    "console/notebooks/notebooks"
    "console/notebooks/build.nu"
    "console/notebooks/bundle.ts"
    "console/notebooks/tsconfig.json"
    "console/notebooks/package.json"
    "console/notebooks/pnpm-lock.yaml"
    "console/notebooks/pnpm-workspace.yaml"
    "console/notebooks/pyproject.toml"
    "console/notebooks/uv.lock"
  ];

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
    EXPECTED_ROWS_FILE = utils.stateFile "expected-rows";
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
    S3_CORS_ORIGINS = urls.notebooks;
    S3_ENDPOINT = "127.0.0.1:${toString (utils.port "seaweedfs" "main")}";
    S3_SECRET_KEY = storage.admin.secretKey;
    S3_USE_SSL = "false";
    SERVING_PASSWORD = postgres.passwords.serving;
    SIGNER_PASSWORD = postgres.passwords.signer;
    SQL_TEMPLATE_DIR = "${config.devenv.root}/helm/files/templates";
    SYNC_CONFIG_PATH = syncConfig;
  };

  setup = utils.mkTaskChain {
    name = "console:setup";
    common.cwd = config.devenv.root;
    after = [
      (utils.processTask "postgres")
      (utils.processTask "seaweedfs")
    ];
    before = [
      (utils.processTask "console")
      (utils.processTask "postgrest")
    ];
    env = setupEnv;
    steps = [
      {
        name = "databases";
        description = "Create the databases and the extensions.";
        command = [
          "psql"
          "--no-psqlrc"
          "--set=ON_ERROR_STOP=1"
          "--dbname=postgres"
          "--file=${utils.stateFile "init.sql"}"
        ];
      }
      {
        name = "bucket";
        description = "Create the storage bucket.";
        command = [
          "nu"
          "helm/files/scripts/ensure-s3-bucket.nu"
          "--create"
        ];
      }
      {
        name = "configure";
        description = "Create the roles and the application schema.";
        command = [
          "nu"
          "helm/files/scripts/configure-db.nu"
        ];
      }
      {
        name = "dbos";
        description = "Create the DBOS tables.";
        command = uv ++ [
          "dbos"
          "migrate"
          "--sys-db-url"
          (utils.postgresUrl { database = appDatabase; })
          "--schema"
          "dbos"
        ];
      }
      {
        name = "migrate";
        description = "Apply the console migrations.";
        command = [
          "goose"
          "-dir"
          "console/migrations"
          "postgres"
          (utils.postgresUrl { database = appDatabase; })
          "up"
        ];
      }
      {
        name = "provision";
        description = "Provision the schemas of the sync settings.";
        command = [
          "nu"
          "helm/files/scripts/provision.nu"
        ];
      }
      {
        name = "seed";
        description = "Seed the test catalog and the table files function.";
        command = uv ++ [
          "python"
          "sync/tests/seeds/console.py"
        ];
      }
    ];
  };

  consoleEnv = {
    COOKIE_SECRET = "test-cookie-secret-test-cookie-secret";
    NOTEBOOK_ORIGIN = urls.notebooks;
    OIDC_CLIENT_ID = "console-web";
    OIDC_CLIENT_SECRET = "test-secret";
    OIDC_ISSUER = urls.issuer;
    ORIGIN = urls.console;
    PORT = appPort;
    POSTGREST_URL = urls.postgrest;
    PROTOCOL_HEADER = "x-forwarded-proto";
    S3_ACCESS_KEY = storage.reader.accessKey;
    S3_ADMIN_ACCESS_KEY = storage.admin.accessKey;
    S3_ADMIN_SECRET_KEY = storage.admin.secretKey;
    S3_BUCKET = s3Bucket;
    S3_ENDPOINT = urls.storage;
    S3_REGION = "us-east-1";
    S3_SECRET_KEY = storage.reader.secretKey;
    CATALOG_URL = utils.postgresUrl {
      database = catalogDatabase;
      user = postgres.roles.signer;
      password = postgres.passwords.signer;
    };
  };

  build = utils.mkTaskChain {
    name = "console:build";
    env = consoleEnv;
    before = [ (utils.processTask "console") ];
    common = {
      cwd = "${config.devenv.root}/console/ui";
      execIfModified = [
        "console/ui/src"
        "console/ui/static"
        "console/ui/package.json"
        "console/ui/pnpm-lock.yaml"
        "console/ui/.npmrc"
        "console/ui/tsconfig.json"
        "console/ui/vite.config.ts"
      ];
    };
    steps = [
      {
        name = "install";
        description = "Install the dependencies of the console app.";
        command = [
          "pnpm"
          "install"
          "--frozen-lockfile"
        ];
      }
      {
        name = "build";
        description = "Build the console server.";
        command = [
          "pnpm"
          "build"
        ];
      }
    ];
  };
in
{
  files = utils.fileEntry "init.sql" {
    text = ''
      CREATE DATABASE ${appDatabase};
      CREATE DATABASE ${catalogDatabase};
      CREATE ROLE duckdb LOGIN PASSWORD '${postgres.passwords.duckdb}';
      ALTER DATABASE ${appDatabase} SET duckdb.unsafe_allow_execution_inside_functions = 'on';
      ALTER DATABASE ${appDatabase} SET duckdb.unsafe_allow_mixed_transactions = 'on';
      \connect ${appDatabase}
      CREATE EXTENSION postgis;
      CREATE EXTENSION pg_duckdb;
    '';
  };

  packages = with pkgs; [
    caddy
    goose
    minijinja
    postgresql_17
    rclone
  ];

  tasks =
    setup.tasks
    // build.tasks
    // {
      "notebooks:build" =
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
          before = [ (utils.processTask "notebooks") ];
          execIfModified = notebookInputs;
        };

      "notebooks:check-site" =
        utils.mkTask {
          command = [
            "nu"
            "build.nu"
            "notebooks/check.py"
            "dist-check"
            "--mode"
            "run"
          ];
        }
        // {
          description = "Build the notebook site that the browser test opens.";
          cwd = "${config.devenv.root}/console/notebooks";
          after = [ "notebooks:build" ];
          before = [ "devenv:enterTest" ];
          execIfModified = notebookInputs;
        };
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
          LISTEN_QUEUES = "control";
        };
      }
      // {
        cwd = config.devenv.root;
        after = [ setup.last ];
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
        ready = utils.probes.http {
          port = appPort;
          path = "/healthz";
        };
      };

    gateway =
      utils.mkProcess {
        command = [
          "caddy"
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
          path = "/healthz";
        };
        after = [ (utils.processTask "console") ];
      };

    notebooks =
      utils.mkProcess {
        command = [
          "node"
          "server.ts"
        ];
        env = {
          PORT = utils.port "notebooks" "main";
          CONSOLE_ORIGIN = urls.console;
          STORAGE_ORIGIN = urls.storage;
        };
      }
      // {
        cwd = "${config.devenv.root}/console/notebooks";
        ports.main.allocate = 13002;
        ready = utils.probes.http {
          port = utils.port "notebooks" "main";
          path = "/config.js";
        };
      };
  };
}
