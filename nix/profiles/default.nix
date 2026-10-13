{
  pkgs,
  ...
}:
{
  profiles.default = {
    extends = [ "checks" ];
    module = {
      env.KUBECONFIG = "k3d/.kubeconfig";

      packages = with pkgs; [
        ast-grep
        kubecolor
        kubectl
        k6
        k3d
        seaweedfs
        (google-cloud-sdk.withExtraComponents (
          with google-cloud-sdk.components; [ gke-gcloud-auth-plugin ]
        ))
      ];

      scripts =
        let
          nushell = file: {
            package = pkgs.nushell;
            binary = "nu";
            exec = file;
          };
        in
        {
          cluster = nushell ../../scripts/cluster.nu;
          token = nushell ../../scripts/token.nu;
        };
    };
  };
}
