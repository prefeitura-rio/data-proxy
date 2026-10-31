{
  imports = [
    ./nix/profiles/base.nix
    ./nix/profiles/repo.nix
    ./nix/profiles/default.nix
    ./nix/profiles/docs.nix
    ./nix/profiles/python.nix
    ./nix/profiles/release.nix
    ./nix/profiles/nix.nix
    ./nix/profiles/int.nix
  ];

  name = "data-proxy";
}
