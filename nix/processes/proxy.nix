{
  pkgs,
  lib,
  config,
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

  host = name: key: "127.0.0.1:${toString (utils.port name key)}";

  renderConf =
    name: instance:
    let
      replacements = {
        "__RUN_DIR__" = utils.stateFile name;
        "__ROOT__" = config.devenv.root;
        "__LUALIB__" = "${pkgs.openresty}/lualib";
        "__SYNC_JSON__" = "${config.devenv.root}/proxy/tests/integration/fixtures/sync.json";
        "__PROXY_PORT__" = toString (utils.port name "main");
        "__POSTGREST_PORT__" = toString (utils.port name "postgrest");
        "__REDIS_PORT__" = toString (
          if instance.ports ? redis then utils.port name "redis" else utils.port "redis" "main"
        );
        "__MAX_INFLIGHT__" = toString instance.maxInflight;
        "__ADMIN_PORT__" = toString (utils.port name "admin");
        "__CONFIG_URL__" = lib.optionalString instance.console "http://${host name "postgrest"}/rpc/config";
      };
    in
    lib.replaceStrings (lib.attrNames replacements) (lib.attrValues replacements) (
      builtins.readFile ../../proxy/tests/integration/fixtures/nginx.conf
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
        port = utils.port name "main";
        path = "/health";
      };
    };
in
{
  env = {
    HURL_VARIABLE_host = host "proxy" "main";
    HURL_VARIABLE_host_redis_down = host "proxy-redis-down" "main";
    HURL_VARIABLE_host_limited = host "proxy-limited" "main";
    HURL_VARIABLE_host_reload = host "proxy-reload" "main";
    HURL_VARIABLE_admin = host "proxy" "admin";
    HURL_VARIABLE_admin_reload = host "proxy-reload" "admin";
    HURL_VARIABLE_console_reload = host "proxy-reload" "postgrest";
  };

  files = lib.mergeAttrsList (
    lib.mapAttrsToList (
      name: instance: utils.fileEntry "${name}.conf" { text = renderConf name instance; }
    ) instances
  );

  tasks = lib.mapAttrs' (
    name: _:
    lib.nameValuePair "proxy:run-dir:${name}" (
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

  processes = lib.mapAttrs mkNginx instances;
}
