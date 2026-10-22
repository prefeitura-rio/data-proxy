{ pkgs, utils, ... }:
let
  realm =
    pkgs.runCommand "realm" { }
      "${pkgs.writers.writeNu "realm" ''
        mkdir $env.out
        open ${../../k3d/values/keycloak.yaml}
        | get realm.configFile
        | str replace --all 'http://console.localhost' '${utils.urls.console}'
        | save $"($env.out)/dev-realm.json"
      ''}";
in
{
  processes.keycloak =
    utils.mkProcess {
      command = utils.podmanRun {
        name = "data-proxy-test-keycloak";
        image = "quay.io/keycloak/keycloak:26.4";
        env = {
          KC_BOOTSTRAP_ADMIN_USERNAME = "admin";
          KC_BOOTSTRAP_ADMIN_PASSWORD = "admin";
        };
        args = [
          "--volume"
          "${realm}:/opt/keycloak/data/import:ro,z"
        ];
        command = [
          "start-dev"
          "--import-realm"
          "--http-port=${toString (utils.port "keycloak" "main")}"
          "--hostname=${utils.urls.keycloak}"
        ];
      };
    }
    // {
      ports.main.allocate = 18180;
      ready = utils.probes.http {
        port = utils.port "keycloak" "main";
        path = "/realms/dev/.well-known/openid-configuration";
      };
    };
}
