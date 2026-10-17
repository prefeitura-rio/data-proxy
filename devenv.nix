{ inputs, ... }:
{
  imports = [ (inputs.import-tree ./nix/profiles) ];

  name = "data-proxy";
}
