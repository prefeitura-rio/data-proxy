{ ... }:
{
  profiles.provider = {
    extends = [ "console" ];
    module =
      {
        config,
        data,
        lib,
        pkgs,
        utils,
        ...
      }:
      let
        sources = utils.mkInputs [
          "provider/go.mod"
          "provider/go.sum"
          "provider/.golangci.yaml"
          "provider/devenv.nix"
          "provider/**/*.go"
        ];
      in
      {
        key = "data-proxy-provider";

        env = {
          CGO_ENABLED = "0";
          GOROOT = "${pkgs.go}/share/go/";
          GOPATH = "${config.devenv.state}/go";
          GOMODCACHE = "${config.devenv.root}/.devenv/cache/go-mod";
          GOCACHE = "${config.devenv.root}/.devenv/cache/go-build";
          GOLANGCI_LINT_CACHE = "${config.devenv.root}/.devenv/cache/golangci-lint";
          GOTOOLCHAIN = "local";
          TF_ACC = "1";
          TF_ACC_TERRAFORM_PATH = lib.getExe pkgs.opentofu;
          TF_ACC_PROVIDER_HOST = "registry.opentofu.org";
          DATA_PROXY_ENDPOINT = data.urls.pgrest;
          DATA_PROXY_TOKEN_URL = "${data.urls.issuer}/protocol/openid-connect/token";
          DATA_PROXY_CLIENT_ID = "terraform";
          DATA_PROXY_CLIENT_SECRET = "test-secret";
        };

        packages = with pkgs; [
          go
          opentofu
          golangci-lint
          gofumpt
          gnupg
          goreleaser
          cocogitto
        ];

        git-hooks.hooks = {
          tf-lint-release = utils.mkHook {
            profile = "provider";
            task = "tf:lint:release";
            files = "^provider/\\.goreleaser\\.yaml$";
          };
          tf-lint-go = utils.mkHook {
            profile = "provider";
            task = "tf:lint:go";
            files = "^provider/.*\\.(go|mod|sum|yaml)$";
          };
          tf-lint-fmt = utils.mkHook {
            profile = "provider";
            task = "tf:lint:fmt";
            files = "^provider/.*\\.go$";
          };
          console-lint-ui.enable = lib.mkForce false;
          console-lint-fmt.enable = lib.mkForce false;
          nb-lint-ts.enable = lib.mkForce false;
          nb-lint-fmt.enable = lib.mkForce false;
        };

        tasks = {
          "tf:lint:release" = {
            description = "Validate the provider release configuration.";
            exec = "goreleaser check --config provider/.goreleaser.yaml";
          };
          "tf:lint:go" = utils.nuTask {
            description = "Lint the Terraform provider.";
            cwd = "provider";
            exec = "golangci-lint run ./...";
            execIfModified = sources;
          };
          "tf:lint:fmt" = utils.nuTask {
            description = "Check the format of the Terraform provider.";
            cwd = "provider";
            exec = ''
              let diff = golangci-lint fmt --diff ./... | complete
              if $diff.exit_code != 0 or ($diff.stdout | str trim | is-not-empty) {
                  print $diff.stdout $diff.stderr
                  error make --unspanned { msg: "Unformatted files. Run golangci-lint fmt ./... in provider." }
              }
            '';
            execIfModified = sources;
          };
        };

        enterTest = lib.mkForce ''
          set -e
          cd provider
          env -u TF_ACC go test ./...
          go test -count=1 -v -run '^TestAcc[A-Z]' ./...
        '';
      };
  };
}
