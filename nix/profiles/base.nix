{
  lib,
  pkgs,
  ...
}:
{
  profiles.base.module =
    { config, ... }:
    {
      imports = [
        {
          key = "data-proxy-utils";
          config._module.args.utils = import ../utils.nix { inherit lib pkgs config; };
        }
      ];

      packages = with pkgs; [
        curl
        goose
        minijinja
        nushell
        postgresql_17
      ];

      env = {
        LD_LIBRARY_PATH = lib.makeLibraryPath [ pkgs.stdenv.cc.cc ];
        NU_LIB_DIRS = "${config.devenv.root}/scripts";
      };
    };
}
