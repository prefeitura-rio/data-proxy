{
  lib,
  pkgs,
  ...
}:
{
  profiles.base.module =
    { config, ... }:
    let
      verbs = [
        "install"
        "build"
        "lint"
        "test"
        "setup"
        "fetch"
        "clean"
        "serve"
        "open"
      ];

      components = [
        "repo"
        "sync"
        "proxy"
        "console"
        "chart"
        "pg"
        "pgrest"
        "kc"
        "s3"
        "nb"
        "nu"
        "ci"
        "ctr"
        "docs"
        "nix"
      ];

      isValidTaskName =
        name:
        let
          parts = lib.splitString ":" name;
          verb = lib.elemAt parts 1;
        in
        lib.hasPrefix "devenv:" name
        || (lib.length parts == 2 && lib.head parts == "repo" && lib.elem verb verbs)
        || (
          lib.length parts == 3
          && lib.elem (lib.head parts) components
          && lib.elem verb verbs
          && builtins.match "[a-z0-9-]+" (lib.elemAt parts 2) != null
        );

      badTaskNames = lib.filter (name: !isValidTaskName name) (lib.attrNames config.tasks);
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
            utils = import ../utils.nix { inherit lib pkgs config; };
          };
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
