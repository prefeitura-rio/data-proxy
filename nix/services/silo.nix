{
  pkgs,
  lib,
  config,
  ...
}:
{
  env.TEST_SILO_ENDPOINT = "127.0.0.1:19000";

  services.minio = lib.mkIf config.devenv.isTesting {
    enable = true;
    package = pkgs.silo;
    listenAddress = "127.0.0.1:19000";
    consoleAddress = "127.0.0.1:19001";
  };
}
