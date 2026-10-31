{ lib, pkgs, ... }:
{
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
          package = pkgs.symlinkJoin {
            name = "uv";
            paths = [ pkgs.uv ];
            nativeBuildInputs = [ pkgs.makeWrapper ];
            postBuild = "wrapProgram $out/bin/uv --prefix LD_LIBRARY_PATH : ${
              lib.makeLibraryPath [ pkgs.stdenv.cc.cc ]
            }";
          };
        };
        lsp.package = pkgs.pyrefly;
      };
    };
}
