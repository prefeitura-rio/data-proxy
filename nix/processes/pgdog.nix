{
  pkgs,
  lib,
  config,
  ...
}:
let
  port = "16432";
  toml = pkgs.formats.toml { };
  settings = toml.generate "pgdog.toml" {
    general = {
      host = "127.0.0.1";
      port = lib.toInt port;
      pooler_mode = "transaction";
      read_write_split = "include_primary_if_replica_banned";
    };
    databases = [
      {
        name = "test";
        host = "127.0.0.1";
        port = lib.toInt config.env.TEST_POSTGRES_PORT;
        role = "primary";
      }
    ];
  };
  users = toml.generate "users.toml" {
    users = [
      {
        name = "test";
        database = "test";
        password = "test";
      }
    ];
  };
in
{
  env.TEST_PGDOG_PORT = port;

  processes.pgdog = lib.mkIf config.devenv.isTesting {
    exec = ''
      exec ${pkgs.pgdog}/bin/pgdog --config ${settings} --users ${users}
    '';
    after = [ "devenv:processes:after-postgres@completed" ];
    ready.exec = "${pkgs.postgresql_17}/bin/pg_isready -h 127.0.0.1 -p ${port}";
  };
}
