{ config, lib, ... }:
{
  imports = [
    ./nix/profiles/base.nix
    ./nix/profiles/checks.nix
    ./nix/profiles/sync.nix
    ./nix/profiles/proxy.nix
    ./nix/profiles/default.nix
    ./nix/profiles/chart.nix
  ];

  name = "data-proxy";

  # devenv keeps test state in .devenv/test-state with a forced value. This priority is the lowest
  # number that wins, and it gives every test service one directory: .devenv/test/<component>.
  devenv.state = lib.mkIf config.devenv.isTesting (
    lib.mkOverride 10 "${config.devenv.root}/.devenv/test"
  );
}
