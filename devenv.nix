{
  imports = [
    ./nix/profiles/shared.nix
    ./nix/profiles/repo.nix
    ./nix/profiles/default.nix
    ./nix/profiles/nix.nix
    ./nix/profiles/integration.nix
    ./nix/profiles/terraform.nix
  ];

  name = "data-proxy";
}
