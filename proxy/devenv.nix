{
  pkgs,
  lib,
  config,
  inputs,
  ...
}:
{
  profiles.proxy = {
    extends = [ "base" ];
    module =
      { utils, ... }:
      {
        imports = [
          ../nix/processes/proxy.nix
          ../nix/services/redis.nix
        ];

        env = {
          LPEG_TYPES = "${inputs.lpeg-types}/library";
          PENLIGHT_TYPES = "${inputs.penlight-types}/library";
        };

        packages = with pkgs; [ lux-cli ];

        languages.lua = {
          enable = true;
          package = pkgs.luajit;
          lsp.package = pkgs.lua-language-server;
        };

        git-hooks.hooks.lx-fmt = {
          enable = true;
          name = "lx fmt";
          entry = "${lib.getExe pkgs.nushell} --include-path ${config.devenv.root}/scripts ${config.devenv.root}/scripts/lux.nu format";
          language = "system";
          files = "^proxy/.*\\.lua$";
          pass_filenames = false;
        };

        tasks = {
          "proxy:rocks" =
            utils.mkTask {
              command = [
                "nu"
                "scripts/lux.nu"
              ];
            }
            // {
              description = "Install the locked Lua rocks of the proxy.";
              before = [ "devenv:enterShell" ];
              execIfModified = utils.inputsOf [
                "proxy/lux.toml"
                "proxy/lux.lock"
                "scripts/lux.nu"
              ];
            };
          "lint:proxy" =
            utils.mkTask {
              command = [
                "nu"
                "scripts/lux.nu"
                "lint"
              ];
            }
            // {
              description = "Lint the Lua code of the proxy.";
              after = [ "proxy:rocks" ];
              execIfModified = utils.inputsOf [
                "proxy/**/*.lua"
                "proxy/lux.toml"
                "proxy/lux.lock"
                "proxy/.luarc.json"
                "proxy/.luacheckrc"
              ];
            };
          "test:proxy" =
            utils.mkTask {
              command = [
                "nu"
                "scripts/lux.nu"
                "test"
              ];
            }
            // {
              description = "Run the unit tests of the proxy.";
              after = [ "proxy:rocks" ];
              execIfModified = utils.inputsOf (
                [
                "proxy/**/*.lua"
                "proxy/lux.toml"
                "proxy/lux.lock"
              ]
                ++ utils.filesOf "proxy/tests/unit/fixtures" [ "json" ]
              );
            };
        };

        enterTest = "${lib.getExe pkgs.hurl} --test --max-time 30 proxy/tests/integration/*.hurl";
      };
  };
}
