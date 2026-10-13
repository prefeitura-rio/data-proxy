{
  pkgs,
  lib,
  config,
  ...
}:
let
  proxy = {
    runDir = "${config.devenv.state}/proxy";
    port = 18080;
    readPort = 18081;
    writePort = 18082;
    redisPort = config.processes.redis.ports.main.value;
  };

  replacements = {
    "__RUN_DIR__" = proxy.runDir;
    "__ROOT__" = config.devenv.root;
    "__LUALIB__" = "${pkgs.openresty}/lualib";
    "__SYNC_JSON__" = "${config.devenv.root}/proxy/tests/integration/fixtures/sync.json";
    "__PROXY_PORT__" = toString proxy.port;
    "__READ_PORT__" = toString proxy.readPort;
    "__WRITE_PORT__" = toString proxy.writePort;
    "__REDIS_PORT__" = toString proxy.redisPort;
    "__LUA_ROCKS__" = "${proxy.runDir}/rocks";
    "__LUA_LIB__" = "${proxy.runDir}/lib";
  };

  nginxConf = pkgs.writeText "nginx.conf" (
    lib.replaceStrings (lib.attrNames replacements) (lib.attrValues replacements) (
      builtins.readFile ../../proxy/tests/integration/fixtures/nginx.conf
    )
  );
in
{
  env.TEST_PROXY_HOST = "127.0.0.1:${toString proxy.port}";

  processes.proxy = lib.mkIf config.devenv.isTesting {
    cwd = config.devenv.root;
    exec = ''
      mkdir -p ${proxy.runDir}/rocks ${proxy.runDir}/lib
      cp -r ${config.devenv.root}/proxy/.lux/jit/*/src/. ${proxy.runDir}/rocks/ 2>/dev/null || true
      cp -r ${config.devenv.root}/proxy/.lux/jit/*/lib/. ${proxy.runDir}/lib/ 2>/dev/null || true
      exec ${pkgs.openresty}/bin/nginx -p ${proxy.runDir} -c ${nginxConf} -g 'daemon off;'
    '';
    ready.http.get = {
      port = proxy.port;
      path = "/health";
    };
  };
}
