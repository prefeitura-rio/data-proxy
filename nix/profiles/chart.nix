{
  pkgs,
  ...
}:
{
  profiles.chart = {
    extends = [ "base" ];
    module = {
      packages = with pkgs; [
        gh
        helmfile
        kubeconform
        kubernetes-helm
      ];

      languages.helm = {
        enable = true;
        plugins = [ "helm-unittest" ];
      };

      tasks = {
        "lint:chart" = {
          exec = "helm lint helm/ --strict -f helm/ci/test-values.yaml";
          execIfModified = [ "helm" ];
        };
        "test:chart" = {
          package = pkgs.nushell;
          exec = builtins.readFile ../../scripts/helm.nu;
          execIfModified = [
            "helm"
            "scripts/helm.nu"
          ];
        };
      };
    };
  };
}
