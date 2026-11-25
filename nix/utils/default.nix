{
  lib,
  pkgs,
  config,
}:
let
  args = {
    inherit
      lib
      pkgs
      config
      utils
      ;
  };

  utils = lib.mergeAttrsList (
    map (file: import file args) [
      ./command.nix
      ./hooks.nix
      ./inputs.nix
      ./processes.nix
      ./state.nix
      ./tasks.nix
    ]
  );
in
utils
