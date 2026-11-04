{ pkgs, ... }:
{
  profiles.default = {
    extends = [
      "repo"
      "console"
      "sync"
      "proxy"
    ];
    module =
      {
        config,
        data,
        utils,
        ...
      }:
      {
        env = {
          KUBECONFIG = "k3d/.kubeconfig";
        };

        packages = with pkgs; [
          ast-grep
          kubecolor
          kubectl
          marksman
          k6
          k3d
          (google-cloud-sdk.withExtraComponents (
            with google-cloud-sdk.components; [ gke-gcloud-auth-plugin ]
          ))
        ];

        scripts = {
          cluster = {
            description = "Run the commands of the local cluster.";
            package = pkgs.nushell;
            binary = "nu";
            exec = ../../scripts/cluster.nu;
            packages = with pkgs; [
              git
              helmfile
            ];
          };
          token = {
            description = "Fetch an access token from a Keycloak realm.";
            package = pkgs.nushell;
            binary = "nu";
            exec = ../../scripts/token.nu;
          };
        };

        processes.docs =
          utils.mkProcess {
            command = [
              "mkdocs"
              "serve"
              "--dev-addr"
              "127.0.0.1:${toString (data.port "docs" "main")}"
            ];
            packages = [ pkgs.mkdocs ];
          }
          // {
            cwd = config.devenv.root;
            ports.main.allocate = 18200;
            ready = utils.probes.http { port = data.port "docs" "main"; };
          };

        tasks = {
          "docs:build:nav".before = [ (utils.processTask "docs") ];
          "docs:build:roles".before = [ (utils.processTask "docs") ];
        };
      };
  };
}
