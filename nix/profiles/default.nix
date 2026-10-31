{ pkgs, ... }:
{
  profiles.default = {
    extends = [
      "docs"
      "console"
      "sync"
      "proxy"
    ];
    module = {
      env.KUBECONFIG = "k3d/.kubeconfig";

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
    };
  };
}
