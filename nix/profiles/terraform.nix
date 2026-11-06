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
          "terraform/.golangci.yml"
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
          gnupg
          golangci-lint
          govulncheck
          zip
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
          "tf:lint:vuln" = utils.nuTask {
            description = "Check the Terraform provider and the Go toolchain against the Go vulnerability database.";
            cwd = "terraform";
            exec = "govulncheck ./...";
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
      {
        lib,
        data,
        pkgs,
        ...
      }:
      {
        # The profiles console and terraform both set the default, so this profile picks one.
        stdenv = pkgs.stdenvNoCC;

        packages = [ pkgs.opentofu ];

        env = {
          TF_ACC = "1";
          TF_ACC_TERRAFORM_PATH = lib.getExe pkgs.opentofu;
          TF_ACC_PROVIDER_HOST = "registry.opentofu.org";
          DATA_PROXY_ENDPOINT = data.urls.pgrest;
          DATA_PROXY_TOKEN_URL = "${data.urls.issuer}/protocol/openid-connect/token";
          DATA_PROXY_CLIENT_ID = "terraform";
          DATA_PROXY_CLIENT_SECRET = "test-secret";
        };

        enterTest = lib.mkForce ''
          cd terraform
          go test -count=1 -v -run '^TestAcc[A-Z]' ./...
        '';
      };
  };
}
