{
  pkgs,
  data,
  utils,
  ...
}:
let
  container = "data-proxy-test-kc";
  containerfile = ../../Containerfile.kc;
  version = builtins.substring 0 12 (builtins.hashFile "sha256" containerfile);
  image = "localhost/${container}:${version}";
  realm =
    pkgs.runCommand "realm" { }
      "${pkgs.writers.writeNu "realm" ''
        mkdir $env.out
        open ${../../k3d/values/keycloak.yaml}
        | get realm.configFile
        | str replace --all 'http://data-proxy.local' '${data.urls.console}'
        | save $"($env.out)/dev-realm.json"
      ''}";
in
{
  tasks."kc:build:image" = utils.nuTask {
    description = "Build the Keycloak test image, with the server build done once. The tag is the hash of the Containerfile, so an existing image is current.";
    exec = ''
      if (podman image exists ${image} | complete).exit_code != 0 {
          podman build --file ${containerfile} --tag ${image} ${pkgs.emptyDirectory}
      }
    '';
    before = [ (utils.processTask "kc") ];
  };

  processes.kc =
    utils.mkProcess {
      command = utils.podmanRun {
        name = container;
        inherit image;
        env = {
          KC_BOOTSTRAP_ADMIN_USERNAME = "admin";
          KC_BOOTSTRAP_ADMIN_PASSWORD = "admin";
        };
        args = [
          "--volume"
          "${realm}:/opt/keycloak/data/import:ro,z"
        ];
        command = [
          "start"
          "--optimized"
          "--http-enabled=true"
          "--cache=local"
          "--import-realm"
          "--http-port=${toString (data.port "kc" "main")}"
          "--hostname=${data.urls.kc}"
        ];
      };
    }
    // {
      ports.main.allocate = 18180;
      ready = utils.probes.http {
        port = data.port "kc" "main";
        path = "/realms/dev/.well-known/openid-configuration";
      };
    };
}
