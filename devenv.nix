{
  imports = [
    ./nix/profiles/base.nix
    ./nix/profiles/checks.nix
    ./nix/profiles/default.nix
    ./nix/profiles/docs.nix
    ./nix/profiles/python.nix
    ./nix/profiles/release.nix
    ./nix/profiles/sizes.nix
    ./nix/profiles/suites.nix
  ];

  name = "data-proxy";
}
