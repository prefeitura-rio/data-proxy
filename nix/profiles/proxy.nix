{
  pkgs,
  config,
  inputs,
  ...
}:
let
  nuLibDirs = "${config.devenv.root}/scripts";
in
{
  profiles.proxy = {
    extends = [ "base" ];
    module = {
      imports = [
        ../processes/proxy.nix
        ../services/redis.nix
      ];

      env = {
        TEST_VALKEY_PORT = "18379";
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
        entry = "bash -c 'cd proxy && lx --lua-version jit fmt'";
        language = "system";
        files = "^proxy/.*\\.lua$";
        pass_filenames = false;
      };

      tasks = {
        "proxy:rocks" = {
          before = [ "devenv:enterShell" ];
          package = pkgs.nushell;
          env.NU_LIB_DIRS = nuLibDirs;
          exec = builtins.readFile ../../scripts/lux.nu;
          execIfModified = [
            "proxy/lux.toml"
            "proxy/lux.lock"
            "scripts/lux.nu"
          ];
        };
        "lint:proxy" = {
          after = [ "proxy:rocks" ];
          cwd = "proxy";
          exec = ''
            lock=$(mktemp)
            cp lux.lock "$lock"
            trap 'cp "$lock" lux.lock; rm -f "$lock"' EXIT
            lx --lua-version jit lint
            ${config.languages.lua.lsp.package}/bin/lua-language-server --check . --configpath .luarc.json --logpath .lux/luals-log --checklevel=Hint
          '';
          execIfModified = [
            "proxy/**/*.lua"
            "proxy/lux.toml"
            "proxy/lux.lock"
            "proxy/.luarc.json"
            "proxy/.luacheckrc"
          ];
        };
        "test:proxy" = {
          after = [ "proxy:rocks" ];
          cwd = "proxy";
          exec = ''
            lock=$(mktemp)
            cp lux.lock "$lock"
            trap 'cp "$lock" lux.lock; rm -f "$lock"' EXIT
            lx --lua-version jit test
          '';
          execIfModified = [
            "proxy/**/*.lua"
            "proxy/tests/unit/fixtures/**"
            "proxy/lux.toml"
            "proxy/lux.lock"
          ];
        };
      };

      enterTest = ''
        set -e
        ${pkgs.hurl}/bin/hurl --test --max-time 30 --variable host=$TEST_PROXY_HOST --variable host_redis_down=$TEST_PROXY_REDIS_DOWN_HOST --variable host_limited=$TEST_PROXY_LIMITED_HOST ${config.devenv.root}/proxy/tests/integration/*.hurl
      '';
    };
  };
}
