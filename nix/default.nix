{
  lib,
  pkgs,
  ...
}:
{
  imports = [
    ./profiles/default.nix
    ./profiles/python.nix
    ./profiles/tools.nix
  ];

  profiles.base.module =
    {
      config,
      ...
    }:
    {
      imports = [
        {
          key = "data-proxy-utils";
          config._module.args = {
            data = import ./data.nix { inherit config; };
            utils = import ./utils { inherit lib pkgs config; };
          };
        }
      ];

      env = {
        NU_LIB_DIRS = "${config.devenv.root}/scripts";
      };

      stdenv = lib.mkDefault pkgs.stdenvNoCC;

      cachix.pull = [ "data-proxy" ];

      packages = with pkgs; [
        (lib.getBin curl)
        (lib.getBin goose)
        (lib.getBin minijinja)
        nushell
        postgresql_18.out
      ];
    };
}
