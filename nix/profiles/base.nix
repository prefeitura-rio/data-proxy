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
        postgresql_17.out
      ];

      env = {
        NU_LIB_DIRS = "${config.devenv.root}/scripts";
      };
    };
}
