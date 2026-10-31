{ pkgs, ... }:
{
  profiles.default = {
    extends = [ "docs" ];
    module =
      { utils, ... }:
      {
        imports = [ ../../console/e2e.nix ];

        env.KUBECONFIG = "k3d/.kubeconfig";

        packages = [
          (utils.nushellCommand "cluster" (
            with pkgs;
            [
              git
              helmfile
            ]
          ))
          (utils.nushellCommand "token" [ ])
        ]
        ++ (with pkgs; [
          ast-grep
          kubecolor
          kubectl
          marksman
          k6
          k3d
          (google-cloud-sdk.withExtraComponents (
            with google-cloud-sdk.components; [ gke-gcloud-auth-plugin ]
          ))
        ]);
      };
  };
}
