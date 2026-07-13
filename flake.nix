{
  description = "poc-pg-duckdb-postgrest - Development Environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs {
          inherit system;
          config.allowUnfree = true;
        };
      in
      {
        devShells.default = pkgs.mkShell {
          name = "poc-pg-duckdb-postgrest-dev";

          buildInputs = with pkgs; [
            python313
            uv
            postgresql_16

            curl
            jq
            just

            git
            docker-compose
          ];

          shellHook = ''
            echo "🦆 poc-pg-duckdb-postgrest Development Environment"
            echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            echo "Python: $(python --version)"
            echo "uv: $(uv --version)"
            echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            echo ""
            echo "  📦 Install sync script deps: uv sync"
            echo "  🐳 Start local stack:        just up"
            echo "  🔁 Run BQ -> GCS -> pg_duckdb sync: just sync"
            echo "  🧪 Demo requests:            just demo"
            echo ""

            if [ -f .env ]; then
              echo "Loading .env file..."
              set -a
              source .env
              set +a
            fi
          '';
        };
      }
    );
}
