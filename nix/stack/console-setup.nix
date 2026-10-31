{
  pkgs,
  config,
  data,
  utils,
  ...
}:
let
  inherit (data.credentials) postgres storage;
  inherit (data) urls;
  postgresPort = data.port "pg" "main";
  appDatabase = data.databases.app;
  catalogDatabase = data.databases.catalog;
  s3Bucket = data.bucket;
  uv = [
    "uv"
    "run"
    "--frozen"
    "--project"
    "sync"
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
          open ${../../k3d/values/data-proxy.yaml} | get sync.config | to json | save --raw $env.out
        ''}";
  };

  setup = utils.mkTaskChain {
    name = "console:setup";
    common.cwd = config.devenv.root;
    after = [
      (utils.processTask "pg")
      (utils.processTask "s3")
    ];
    before = [
      (utils.processTask "console")
      (utils.processTask "pgrest")
    ];
    env = setupEnv;
    steps = [
      {
        name = "db";
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
        packages = with pkgs; [ rclone ];
        command = [
          "nu"
          "helm/files/scripts/ensure-s3-bucket.nu"
          "--create"
        ];
      }
      {
        name = "conf";
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
        name = "prov";
        description = "Provision the schemas of the sync settings.";
        packages = with pkgs; [ rclone ];
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

  tasks = setup.tasks;

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
  };
}
