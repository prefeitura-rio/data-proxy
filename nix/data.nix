{ config }:
let
  port = process: name: config.processes.${process}.ports.${name}.value;
  keycloak = "http://localhost:${toString (port "keycloak" "main")}";
  notebooksPort = 13002;
in
{
  inherit port notebooksPort;

  urls = {
    inherit keycloak;
    console = "http://console.localhost:${toString (port "gateway" "main")}";
    notebooks = "http://notebooks.localhost:${toString notebooksPort}";
    storage = "http://localhost:${toString (port "seaweedfs" "main")}";
    issuer = "${keycloak}/realms/dev";
    postgrest = "http://127.0.0.1:${toString (port "postgrest" "main")}";
  };

  credentials = {
    postgres = {
      user = "test";
      password = "test";
      roles = {
        anon = "anon";
        user = "dp_api";
        serving = "serving";
        signer = "console_signer";
      };
      passwords = {
        duckdb = "test-duckdb";
        serving = "test-serving";
        signer = "test-signer";
      };
    };
    storage = {
      admin = {
        accessKey = "seaweedfs";
        secretKey = "seaweedfs";
      };
      reader = {
        accessKey = "data-proxy-reader";
        secretKey = "data-proxy-reader-secret";
      };
    };
  };
}
