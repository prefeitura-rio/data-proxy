{ pkgs, ... }:
{
  profiles.chart = {
    extends = [ "base" ];
    module =
      { utils, ... }:
      {
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
          "lint:chart" = utils.nuTask {
            description = "Lint the Helm chart.";
            exec = "helm lint helm/ --strict -f helm/ci/test-values.yaml";
            execIfModified = [ "helm" ];
          };
          "test:chart" = utils.nuTask {
            description = "Run the Helm chart tests.";
            exec = builtins.readFile ../scripts/helm.nu;
            execIfModified = [
              "helm"
              "scripts/helm.nu"
            ];
          };
        };
      };
  };
}
