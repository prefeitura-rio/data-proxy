{ lib, pkgs, ... }:
{
  profiles.terraform = {
    extends = [ "base" ];
    module =
      { utils, ... }:
      let
        sources = utils.mkInputs [
          "terraform/go.mod"
          "terraform/go.sum"
          "terraform/**/*.go"
        ];
      in
      {
        stdenv = lib.mkDefault pkgs.stdenvNoCC;

        env = {
          CGO_ENABLED = "0";
        };

        packages = with pkgs; [
          go
          golangci-lint
          terraform
          terraform-plugin-docs
        ];

        tasks = {
          "tf:lint:go" = utils.nuTask {
            description = "Check the format of the Terraform provider and lint it.";
            cwd = "terraform";
            exec = ''
              use lib.nu [fail]

              let unformatted = gofmt -l . | lines
              if ($unformatted | is-not-empty) {
                  fail $"gofmt would change these files: ($unformatted | str join ', ')"
              }
              golangci-lint run ./...
            '';
            execIfModified = sources;
          };
          "tf:test:unit" = utils.nuTask {
            description = "Run the unit tests of the Terraform provider, without the acceptance tests.";
            cwd = "terraform";
            exec = ''
              hide-env --ignore-errors TF_ACC
              go test ./...
            '';
            execIfModified = sources;
          };
          "tf:test:int" = utils.serviceSuite {
            description = "Run the acceptance tests of the Terraform provider against the console stack.";
            profile = "terraform-acc";
            inputs = [
              "devenv.nix"
              "devenv.lock"
              "devenv.yaml"
              "nix/utils/*.nix"
              "nix/data.nix"
              "nix/profiles/*.nix"
              "nix/stack/console.nix"
              "nix/stack/keycloak.nix"
              "nix/stack/postgres.nix"
              "nix/stack/postgrest.nix"
              "nix/stack/seaweedfs.nix"
              "console/devenv.nix"
              "console/migrations/*.sql"
              "k3d/values/keycloak.yaml"
              "Containerfile.kc"
              "Containerfile.pg"
            ]
            ++ utils.mkGlobs "helm/files" [
              "sql"
              "nu"
              "toml"
              "json"
              "conf"
            ]
            ++ sources;
          };
        };
      };
  };

  # Starts the console stack and runs the acceptance tests of the provider with `devenv --profile terraform-acc test`.
  # The provider calls PostgREST directly with the test client `admin`, whose role is dp_admin.
  profiles.terraform-acc = {
    extends = [
      "console"
      "terraform"
    ];
    module =
      { lib, data, ... }:
      {
        env = {
          TF_ACC = "1";
          DATAPROXY_ENDPOINT = data.urls.pgrest;
          DATAPROXY_TOKEN_URL = "${data.urls.issuer}/protocol/openid-connect/token";
          DATAPROXY_CLIENT_ID = "admin";
          DATAPROXY_CLIENT_SECRET = "test-secret";
        };

        enterTest = lib.mkForce ''
          cd terraform
          go test -count=1 -run '^TestAcc[A-Z]' ./...
        '';
      };
  };
}
