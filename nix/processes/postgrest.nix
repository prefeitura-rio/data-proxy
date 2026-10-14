{
  pkgs,
  lib,
  config,
  ...
}:
{
  env = {
    TEST_POSTGREST_URL = "http://127.0.0.1:13000";
    TEST_POSTGREST_AUTH = "postgrest-test-secret-012345678901234567890123";
  };

  processes.postgrest = lib.mkIf config.devenv.isTesting {
    exec = ''
      [ -e ${config.env.TEST_DATABASE_READY} ] || { echo "database bootstrap failed" >&2; exit 1; }
      exec ${pkgs.postgrest}/bin/postgrest
    '';
    after = [ "devenv:processes:after-postgres@completed" ];
    env = {
      PGRST_DB_URI = "postgres://test:test@127.0.0.1:${config.env.TEST_POSTGRES_PORT}/test";
      PGRST_DB_SCHEMAS = "pushdown,admin";
      PGRST_DB_ANON_ROLE = "anon";
      PGRST_JWT_SECRET = config.env.TEST_POSTGREST_AUTH;
      PGRST_DB_PREPARED_STATEMENTS = "false";
      PGRST_DB_CHANNEL_ENABLED = "false";
      PGRST_SERVER_HOST = "127.0.0.1";
      PGRST_SERVER_PORT = "13000";
    };
    ready.http.get = {
      port = 13000;
      path = "/rpc/items";
    };
  };
}
