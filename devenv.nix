{
  imports = [
    ./nix/profiles/base.nix
    ./nix/profiles/checks.nix
    ./nix/profiles/default.nix
    ./nix/profiles/python.nix
  ];

  name = "data-proxy";
}
