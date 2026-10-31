{ lib, pkgs, ... }:
{
  profiles.release = {
    extends = [ "chart" ];
    module = {
      stdenv = lib.mkDefault pkgs.stdenvNoCC;

      packages = with pkgs; [ cocogitto ];

      scripts.ci = {
        description = "Compute the CI change flags and publish the chart and the images.";
        package = pkgs.nushell;
        binary = "nu";
        exec = ../../scripts/ci.nu;
        packages = with pkgs; [
          git
          go-containerregistry
        ];
      };
    };
  };
}
