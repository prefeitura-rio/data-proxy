{
  description = "Data Proxy CI tools";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

  outputs =
    { nixpkgs, ... }:
    let
      systems = [ "x86_64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      devShells = forAllSystems (
        system:
        let
          pkgs = import nixpkgs { inherit system; };
          helm =
            with pkgs;
            wrapHelm kubernetes-helm {
              plugins = with kubernetes-helmPlugins; [ helm-unittest ];
            };
          mkShell = packages: pkgs.mkShell { inherit packages; };
        in
        {
          quality = mkShell (
            with pkgs;
            [
              actionlint
              helm
              kubeconform
              nu-lint
              sqlfluff
              typescript
            ]
          );

          build = mkShell (with pkgs; [ hadolint ]);

          release = mkShell (
            with pkgs;
            [
              helm
              yq-go
            ]
          );
        }
      );
    };
}
