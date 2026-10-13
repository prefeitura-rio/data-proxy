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
    };
    proxy-redis-down = {
      port = 18090;
      readPort = 18091;
      writePort = 18092;
      redisPort = 18399;
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
        "__SYNC_JSON__" = "${config.devenv.root}/proxy/tests/integration/fixtures/sync.json";
        "__PROXY_PORT__" = toString instance.port;
        "__READ_PORT__" = toString instance.readPort;
        "__WRITE_PORT__" = toString instance.writePort;
        "__REDIS_PORT__" = toString instance.redisPort;
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

  processes = lib.mkIf config.devenv.isTesting (lib.mapAttrs mkProcess instances);
}
