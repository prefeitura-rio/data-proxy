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
      utils,
      ...
    }:
    let
      badTaskNames = utils.badTaskNames (lib.attrNames config.tasks);
    in
    {
      assertions = [
        {
          assertion = badTaskNames == [ ];
          message = "These task names are not <component>:<verb>:<target> from the glossary of the style guide: ${lib.concatStringsSep ", " badTaskNames}";
        }
      ];

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
