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
        stdenv = lib.mkDefault pkgs.stdenvNoCC;

        packages = with pkgs; [ cocogitto ];

        languages.helm = {
          enable = true;
          plugins = [ "helm-unittest" ];
        };

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
          "chart:test:unit" = utils.nuTask {
            description = "Run the Helm chart tests.";
            packages = with pkgs; [ kubeconform ];
            exec = builtins.readFile ../scripts/helm.nu;
            execIfModified = utils.mkInputs ([ "scripts/helm.nu" ] ++ chartFiles);
          };
        };
      };
  };
}
