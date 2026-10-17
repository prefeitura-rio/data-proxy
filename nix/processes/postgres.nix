{
  pkgs,
  lib,
  config,
  ...
}:
let
  port = "15432";
  container = "data-proxy-test-postgres";
  inherit (import ../lib.nix { inherit lib; }) mkPodmanProcess;
in
{
  env = {
    TEST_POSTGRES_PORT = port;
    TEST_POSTGRES_CONTAINER = container;
    TEST_DATABASE_READY = "${config.devenv.state}/database-ready";
  };

  processes.postgres = lib.mkIf config.devenv.isTesting (mkPodmanProcess {
    name = container;
    build.file = ../../Containerfile.postgres;
    env = {
      POSTGRES_USER = "test";
      POSTGRES_PASSWORD = "test";
      POSTGRES_DB = "test";
    };
    # The image entrypoint creates the data directory and gives it to the postgres user, then drops root. It needs
    # root for this. Without it, the data directory can belong to root in older rootless podman, and initdb fails.
    args = [
      "--user"
      "root"
    ];
    command = [
      "postgres"
      "-p"
      port
      "-c"
      "shared_preload_libraries=pg_duckdb"
      "-c"
      "duckdb.extension_directory=/usr/local/lib/pg_duckdb/extensions"
      "-c"
      "duckdb.allow_unsigned_extensions=on"
      "-c"
      "duckdb.allow_community_extensions=on"
      "-c"
      "duckdb.unsafe_allow_execution_inside_functions=on"
    ];
    ready = "pg_isready -p ${port} -U test";
  });

  processes.after-postgres = lib.mkIf config.devenv.isTesting {
    exec = ''
      rm -f ${config.env.TEST_DATABASE_READY}
      ${pkgs.nushell}/bin/nu ${config.devenv.root}/scripts/db.nu && touch ${config.env.TEST_DATABASE_READY}
    '';
    after = [ "devenv:processes:postgres" ];
    restart.on = "never";
  };
}
