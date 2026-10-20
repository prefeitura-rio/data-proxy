{ pkgs, ... }:
{
  profiles.default = {
    extends = [ "checks" ];
    module =
      { utils, ... }:
      {
        env.KUBECONFIG = "k3d/.kubeconfig";

        packages = [
          (utils.nushellCommand "cluster")
          (utils.nushellCommand "token")
        ]
        ++ (with pkgs; [
          ast-grep
          kubecolor
          kubectl
          k6
          k3d
          seaweedfs
          (google-cloud-sdk.withExtraComponents (
            with google-cloud-sdk.components; [ gke-gcloud-auth-plugin ]
          ))
        ]);
      };
  };
}
