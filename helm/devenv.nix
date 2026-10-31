{ pkgs, ... }:
{
  profiles.chart = {
    extends = [ "base" ];
    module =
      { utils, ... }:
      {
        languages.helm = {
          enable = true;
          plugins = [ "helm-unittest" ];
        };

        tasks = {
          "chart:lint:helm" = utils.nuTask {
            description = "Lint the Helm chart.";
            exec = "helm lint helm/ --strict -f helm/ci/test-values.yaml";
            execIfModified = utils.mkInputs (
              utils.mkGlobs "helm" [
                "yaml"
                "sql"
                "nu"
                "tpl"
                "toml"
                "nix"
                "lock"
                "json"
                "conf"
              ]
            );
          };
          "chart:tests:unit" = utils.nuTask {
            description = "Run the Helm chart tests.";
            packages = with pkgs; [ kubeconform ];
            exec = builtins.readFile ../scripts/helm.nu;
            execIfModified = utils.mkInputs (
              [ "scripts/helm.nu" ]
              ++ utils.mkGlobs "helm" [
                "yaml"
                "sql"
                "nu"
                "tpl"
                "toml"
                "nix"
                "lock"
                "json"
                "conf"
              ]
            );
          };
        };
      };
  };
}
