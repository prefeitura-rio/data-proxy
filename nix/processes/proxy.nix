{
  pkgs,
  lib,
  config,
  ...
}:
let
  instances = {
    proxy = {
      port = 18080;
      readPort = 18081;
      writePort = 18082;
      redisPort = config.processes.redis.ports.main.value;
      maxInflight = 0;
      refreshSeconds = 3600;
    };
    proxy-redis-down = {
      port = 18090;
      readPort = 18091;
      writePort = 18092;
      redisPort = 18399;
      maxInflight = 0;
      refreshSeconds = 3600;
    };
    proxy-limited = {
      port = 18100;
      readPort = 18101;
      writePort = 18102;
      redisPort = config.processes.redis.ports.main.value;
      maxInflight = 1;
      refreshSeconds = 3600;
    };
    proxy-reload = {
      port = 18110;
      readPort = 18111;
      writePort = 18112;
      redisPort = config.processes.redis.ports.main.value;
      maxInflight = 0;
      refreshSeconds = 1;
    };
  };

  mkProcess =
    name: instance:
    let
      runDir = "${config.devenv.state}/${name}";

      replacements = {
        "__RUN_DIR__" = runDir;
        "__ROOT__" = config.devenv.root;
        "__LUALIB__" = "${pkgs.openresty}/lualib";
        "__SYNC_PATH__" =
          if instance.refreshSeconds == 1 then "${runDir}/sync.json" else "${config.devenv.root}/proxy/tests/integration/fixtures/sync.json";
        "__PROXY_PORT__" = toString instance.port;
        "__READ_PORT__" = toString instance.readPort;
        "__WRITE_PORT__" = toString instance.writePort;
        "__REDIS_PORT__" = toString instance.redisPort;
        "__MAX_INFLIGHT__" = toString instance.maxInflight;
        "__REFRESH_SECONDS__" = toString instance.refreshSeconds;
        "__LUA_ROCKS__" = "${runDir}/rocks";
        "__LUA_LIB__" = "${runDir}/lib";
      };

      nginxConf = pkgs.writeText "${name}.conf" (
        lib.replaceStrings (lib.attrNames replacements) (lib.attrValues replacements) (
          builtins.readFile ../../proxy/tests/integration/fixtures/nginx.conf
        )
      );
    in
    {
      cwd = config.devenv.root;
      exec = ''
        mkdir -p ${runDir}/rocks ${runDir}/lib
        cp -f ${config.devenv.root}/proxy/tests/integration/fixtures/sync.json ${runDir}/sync.json
        cp -r ${config.devenv.root}/proxy/.lux/jit/*/src/. ${runDir}/rocks/ 2>/dev/null || true
        cp -r ${config.devenv.root}/proxy/.lux/jit/*/lib/. ${runDir}/lib/ 2>/dev/null || true
        exec ${pkgs.openresty}/bin/nginx -p ${runDir} -c ${nginxConf} -g 'daemon off;'
      '';
      ready.http.get = {
        port = instance.port;
        path = "/health";
      };
    };
in
{
  env.TEST_PROXY_HOST = "127.0.0.1:${toString instances.proxy.port}";
  env.TEST_PROXY_REDIS_DOWN_HOST = "127.0.0.1:${toString instances.proxy-redis-down.port}";
  env.TEST_PROXY_LIMITED_HOST = "127.0.0.1:${toString instances.proxy-limited.port}";
  env.TEST_PROXY_RELOAD_HOST = "127.0.0.1:${toString instances.proxy-reload.port}";
  env.TEST_PROXY_RELOAD_SYNC_HOST = "127.0.0.1:${toString instances.proxy-reload.readPort}";

  processes = lib.mkIf config.devenv.isTesting (lib.mapAttrs mkProcess instances);
}
