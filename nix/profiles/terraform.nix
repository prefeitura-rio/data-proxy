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
        };
      };
  };
}
