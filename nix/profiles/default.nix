{
  pkgs,
  config,
  ...
}:
let
  nushellCommand =
    name:
    let
      script = pkgs.runCommand name { } ''
        mkdir -p $out
        cp ${../../scripts + "/${name}.nu"} $out/${name}
      '';
    in
    pkgs.writeShellScriptBin name ''
      exec ${pkgs.nushell}/bin/nu --include-path ${config.devenv.root}/scripts ${script}/${name} "$@"
    '';
in
{
  profiles.default = {
    extends = [ "checks" ];
    module = {
      env.KUBECONFIG = "k3d/.kubeconfig";

      packages = [
        (nushellCommand "cluster")
        (nushellCommand "token")
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
