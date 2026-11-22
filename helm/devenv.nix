{ lib, pkgs, ... }:
{
  profiles.chart = {
    extends = [ "base" ];
    module =
      { utils, ... }:
      let
        chartFiles = utils.mkGlobs "helm" [
          "yaml"
          "sql"
          "nu"
          "tpl"
          "toml"
          "nix"
          "lock"
          "json"
          "conf"
        ];
      in
      {

        packages = with pkgs; [
          cocogitto
          kubeconform
        ];

        languages.helm = {
          enable = true;
          plugins = [ "helm-unittest" ];
          lsp.enable = lib.mkDefault false;
        };

        git-hooks.hooks = {
          chart-lint-helm = utils.mkHook {
            profile = "chart";
            task = "chart:lint:helm";
            files = "^helm/.*\\.(yaml|tpl|sql|nu|toml|json|conf)$";
          };
          chart-lint-toml = utils.mkHook {
            profile = "chart";
            task = "chart:lint:toml";
            files = "^(helm/files/config/rbac\\.(toml|schema\\.json)|\\.taplo\\.toml)$";
          };
        };

        enterTest = "nu scripts/helm.nu";

        scripts.ci = {
          description = "Compute the CI change flags and publish the chart and the images.";
          package = pkgs.nushell;
          binary = "nu";
          exec = ../scripts/ci.nu;
          packages = with pkgs; [
            git
            go-containerregistry
          ];
        };

        tasks = {
          "chart:lint:helm" = utils.nuTask {
            description = "Lint the Helm chart.";
            exec = "helm lint helm/ --strict -f helm/ci/test-values.yaml";
            execIfModified = utils.mkInputs chartFiles;
          };
          "chart:lint:toml" = utils.nuTask {
            description = "Lint the RBAC rules file.";
            packages = with pkgs; [ taplo ];
            exec = ''
              taplo lint helm/files/config/rbac.toml
              taplo fmt --check helm/files/config/rbac.toml
            '';
            execIfModified = utils.mkInputs [
              "helm/files/config/rbac.toml"
              "helm/files/config/rbac.schema.json"
              ".taplo.toml"
            ];
          };
        };
      };
  };
}
