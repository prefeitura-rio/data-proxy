{
  lib,
  pkgs,
  ...
}:
{
  profiles.base.module =
    {
      config,
      inputs,
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
            data = import ../data.nix { inherit config; };
            utils = import ../utils {
              inherit lib pkgs config;
              inherit (inputs) import-tree;
            };
          };
        }
      ];

      packages = with pkgs; [
        curl
        goose
        minijinja
        nushell
        postgresql_18.out
      ];

      env = {
        NU_LIB_DIRS = "${config.devenv.root}/scripts";
      };
    };

  profiles.python.module =
    { config, ... }:
    {
      env = {
        UV_LINK_MODE = "hardlink";
        UV_PROJECT_ENVIRONMENT = lib.mkForce "${config.devenv.root}/sync/.venv";
      };

      languages.python = {
        enable = true;
        package = pkgs.python314;
        uv = {
          enable = true;
          package =
            with pkgs;
            symlinkJoin {
              name = "uv";
              paths = [ uv ];
              nativeBuildInputs = [ makeWrapper ];
              postBuild = "wrapProgram $out/bin/uv --prefix LD_LIBRARY_PATH : ${
                lib.makeLibraryPath [ stdenv.cc.cc ]
              }";
            };
        };
        lsp.package = pkgs.pyrefly;
      };
    };
}
