{
  lib,
  pkgs,
  config,
  import-tree,
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
    (import-tree.filterNot (lib.hasSuffix "/default.nix")).pipeTo (map (file: import file args)) ./.
  );
in
utils
