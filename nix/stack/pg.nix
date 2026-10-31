{
  pkgs,
  data,
  utils,
  ...
}:
let
  inherit (data.credentials) postgres;
  container = "data-proxy-test-pg";
  image = "localhost/${container}:dev";
  port = toString (data.port "pg" "main");
in
{
  env = {
    TEST_POSTGRES_PORT = port;
    TEST_POSTGRES_CONTAINER = container;
  };

  tasks."pg:build:image" =
    utils.mkTask {
      command = [
        "podman"
        "build"
        "--file"
        "${../../Containerfile.pg}"
        "--tag"
        image
        "${pkgs.emptyDirectory}"
      ];
    }
    // {
      description = "Build the PostgreSQL test image.";
      before = [ (utils.processTask "pg") ];
      execIfModified = utils.mkInputs [ "Containerfile.pg" ];
    };

  processes.pg =
    utils.mkProcess {
      command = utils.podmanRun {
        name = container;
        inherit image;
        env = {
          POSTGRES_USER = postgres.user;
          POSTGRES_PASSWORD = postgres.password;
          POSTGRES_DB = "test";
        };
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
      };
    }
    // {
      ports.main.allocate = 15432;
      ready = utils.probes.command [
        "podman"
        "exec"
        container
        "pg_isready"
        "-p"
        port
        "-U"
        postgres.user
      ];
    };
}
