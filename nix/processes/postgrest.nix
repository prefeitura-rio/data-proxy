{ pkgs, utils, ... }:
let
  inherit (utils.credentials) postgres;
  jwks = utils.stateFile "jwks.json";
in
{
  tasks."postgrest:jwks" =
    utils.mkTask {
      packages = with pkgs; [ curl ];
      command = [
        "curl"
        "--fail"
        "--silent"
        "--create-dirs"
        "--output"
        jwks
        "${utils.urls.issuer}/protocol/openid-connect/certs"
      ];
    }
    // {
      description = "Download the signing keys of the test Keycloak.";
      after = [ (utils.processTask "keycloak") ];
      before = [ (utils.processTask "postgrest") ];
    };

  processes.postgrest =
    utils.mkProcess {
      packages = with pkgs; [ postgrest ];
      command = [ "postgrest" ];
      env = {
        PGRST_DB_URI = utils.postgresUrl {
          database = "data_proxy";
          user = postgres.roles.serving;
          password = postgres.passwords.serving;
        };
        PGRST_SERVER_PORT = utils.port "postgrest" "main";
        PGRST_DB_SCHEMAS = "console";
        PGRST_DB_ANON_ROLE = postgres.roles.anon;
        PGRST_JWT_ROLE_CLAIM_KEY = ".role";
        PGRST_DB_PRE_REQUEST = "data_proxy.pre_request";
        PGRST_DB_PREPARED_STATEMENTS = "false";
        PGRST_DB_CHANNEL_ENABLED = "true";
        PGRST_JWT_SECRET = "@${jwks}";
        PGRST_DB_MAX_ROWS = "1000";
        PGRST_DB_AGGREGATES_ENABLED = "true";
        PGRST_OPENAPI_SECURITY_ACTIVE = "true";
      };
    }
    // {
      ports.main.allocate = 13000;
      ready = utils.probes.http { port = utils.port "postgrest" "main"; };
    };
}
