{ ... }:
{
  profiles.proxy = {
    extends = [ "base" ];
    module =
      {
        pkgs,
        lib,
        config,
        data,
        inputs,
        utils,
        ...
      }:
      let
        instances = {
          proxy = {
            ports = {
              main = 18080;
              postgrest = 18081;
              admin = 18083;
            };
            maxInflight = 0;
            console = false;
          };
          proxy-redis-down = {
            ports = {
              main = 18090;
              postgrest = 18091;
              admin = 18093;
              redis = 18399;
            };
            maxInflight = 0;
            console = false;
            startAfter = "proxy";
          };
          proxy-limited = {
            ports = {
              main = 18100;
              postgrest = 18101;
              admin = 18103;
            };
            maxInflight = 1;
            console = false;
            startAfter = "proxy-redis-down";
          };
          proxy-reload = {
            ports = {
              main = 18110;
              postgrest = 18111;
              admin = 18113;
            };
            maxInflight = 0;
            console = true;
            startAfter = "proxy-limited";
          };
        };

        host = name: key: "127.0.0.1:${toString (data.port name key)}";

        renderConf =
          name: instance:
          let
            replacements = {
              "__RUN_DIR__" = utils.stateFile name;
              "__ROOT__" = config.devenv.root;
              "__LUALIB__" = "${pkgs.openresty}/lualib";
              "__SYNC_JSON__" = "${config.devenv.root}/proxy/tests/integration/fixtures/sync.json";
              "__PROXY_PORT__" = toString (data.port name "main");
              "__POSTGREST_PORT__" = toString (data.port name "postgrest");
              "__REDIS_PORT__" = toString (
                if instance.ports ? redis then data.port name "redis" else data.port "redis" "main"
              );
              "__MAX_INFLIGHT__" = toString instance.maxInflight;
              "__ADMIN_PORT__" = toString (data.port name "admin");
              "__CONFIG_URL__" = lib.optionalString instance.console "http://${host name "postgrest"}/rpc/config";
            };
          in
          lib.replaceStrings (lib.attrNames replacements) (lib.attrValues replacements) (
            builtins.readFile ./tests/integration/fixtures/nginx.conf
          );

        mkNginx =
          name: instance:
          utils.mkProcess {
            command = [
              "lx"
              "--lua-version"
              "jit"
              "exec"
              "--no-loader"
              "--"
              (lib.getExe' pkgs.openresty "nginx")
              "-p"
              (utils.stateFile name)
              "-c"
              (utils.stateFile "${name}.conf")
              "-g"
              "daemon off;"
            ];
          }
          // {
            cwd = "${config.devenv.root}/proxy";
            ports = lib.mapAttrs (_: base: { allocate = base; }) instance.ports;
            after = [
              (utils.processTask "redis")
            ]
            ++ lib.optional (instance ? startAfter) (utils.processTask instance.startAfter);
            ready = utils.probes.http {
              port = data.port name "main";
              path = "/health";
            };
          };

        setupTasks = lib.mapAttrs' (
          name: _:
          lib.nameValuePair "proxy:setup:${name}" (
            utils.mkTask {
              command = [
                "mkdir"
                "--parents"
                (utils.stateFile name)
              ];
            }
            // {
              description = "Create the run folder of ${name}.";
              before = [ (utils.processTask name) ];
            }
          )
        ) instances;
      in
      {
        key = "data-proxy-proxy";
        imports = [ ../nix/services/redis.nix ];

        stdenv = lib.mkOverride 900 pkgs.stdenv;

        env = {
          LPEG_TYPES = "${inputs.lpeg-types}/library";
          PENLIGHT_TYPES = "${inputs.penlight-types}/library";
        };

        packages = with pkgs; [ lux-cli ];

        languages.lua = {
          enable = true;
          package = pkgs.luajit;
          lsp.enable = lib.mkDefault false;
        };

        git-hooks.hooks = {
          proxy-lint-lua = utils.mkHook {
            profile = "proxy";
            task = "proxy:lint:lua";
            files = "^(proxy/.*\\.lua|proxy/lux\\.(toml|lock)|proxy/\\.luarc\\.json)$";
          };
          proxy-lint-fmt = utils.mkHook {
            profile = "proxy";
            task = "proxy:lint:fmt";
            files = "^proxy/.*\\.lua$";
          };
        };

        tasks = setupTasks // {
          "proxy:install:rocks" =
            utils.mkTask {
              command = [
                "nu"
                "scripts/lux.nu"
              ];
            }
            // {
              description = "Install the locked Lua rocks of the proxy.";
              before = [ "devenv:enterShell" ];
              execIfModified = utils.mkInputs [
                "proxy/lux.toml"
                "proxy/lux.lock"
                "scripts/lux.nu"
              ];
            };
          "proxy:lint:fmt" =
            utils.mkTask {
              command = [
                "nu"
                "scripts/lux.nu"
                "format"
                "--check"
              ];
              packages = [ pkgs.stylua ];
            }
            // {
              description = "Check the format of the Lua code of the proxy.";
              execIfModified = utils.mkInputs [
                "proxy/**/*.lua"
                "proxy/.stylua.toml"
              ];
            };
          "proxy:lint:lua" =
            utils.mkTask {
              command = [
                "nu"
                "scripts/lux.nu"
                "lint"
              ];
              packages = [ pkgs.lua-language-server ];
            }
            // {
              description = "Lint the Lua code of the proxy.";
              after = [ "proxy:install:rocks" ];
              execIfModified = utils.mkInputs [
                "proxy/**/*.lua"
                "proxy/lux.toml"
                "proxy/lux.lock"
                "proxy/.luarc.json"
                "proxy/.luacheckrc"
              ];
            };
        };

        env = {
          HURL_VARIABLE_host = host "proxy" "main";
          HURL_VARIABLE_host_redis_down = host "proxy-redis-down" "main";
          HURL_VARIABLE_host_limited = host "proxy-limited" "main";
          HURL_VARIABLE_host_reload = host "proxy-reload" "main";
          HURL_VARIABLE_admin = host "proxy" "admin";
          HURL_VARIABLE_admin_reload = host "proxy-reload" "admin";
          HURL_VARIABLE_console_reload = host "proxy-reload" "postgrest";
          HURL_VARIABLE_upstream = host "proxy" "postgrest";
        };

        files = lib.mergeAttrsList (
          lib.mapAttrsToList (
            name: instance: utils.fileEntry "${name}.conf" { text = renderConf name instance; }
          ) instances
        );

        processes = lib.mapAttrs mkNginx instances;

        enterTest = ''
          set -e
          nu scripts/lux.nu test
          ${lib.getExe pkgs.hurl} --test --max-time 30 proxy/tests/integration/*.hurl
        '';
      };
  };
}
