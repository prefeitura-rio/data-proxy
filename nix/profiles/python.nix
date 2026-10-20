{ lib, pkgs, ... }:
{
  profiles.python.module =
    { config, ... }:
    {
      env = {
        UV_LINK_MODE = "copy";
        UV_PROJECT_ENVIRONMENT = lib.mkForce "${config.devenv.root}/sync/.venv";
      };

      languages.python = {
        enable = true;
        package = pkgs.python314;
        uv.enable = true;
      };
    };
}
