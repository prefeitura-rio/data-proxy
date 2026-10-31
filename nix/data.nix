{ config }:
let
  port = process: name: config.processes.${process}.ports.${name}.value;
  kc = "http://localhost:${toString (port "kc" "main")}";
  notebooksPort = 13002;
in
{
  inherit port notebooksPort;

  urls = {
    inherit kc;
    console = "http://console.localhost:${toString (port "gateway" "main")}";
    nb = "http://notebooks.localhost:${toString notebooksPort}";
    s3 = "http://localhost:${toString (port "s3" "main")}";
    issuer = "${kc}/realms/dev";
    pgrest = "http://127.0.0.1:${toString (port "pgrest" "main")}";
  };

  databases = {
    app = "data_proxy";
    catalog = "ducklake";
  };

  bucket = "test-bucket";

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
