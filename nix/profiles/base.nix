{
  lib,
  pkgs,
  config,
  ...
}:
{
  profiles.base.module = {
    env = {
      LD_LIBRARY_PATH = lib.makeLibraryPath [ pkgs.stdenv.cc.cc ];
      NU_LIB_DIRS = "${config.devenv.root}/scripts";
    };
  };
}
