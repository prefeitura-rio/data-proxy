{
  pkgs,
  lib,
  config,
  ...
}:
{
  env.TEST_VALKEY_PORT = lib.mkDefault "16379";

  services.redis = lib.mkIf config.devenv.isTesting {
    enable = true;
    package = pkgs.valkey;
    port = lib.toInt config.env.TEST_VALKEY_PORT;
    extraConfig = ''
      locale-collate C
      save ""
    '';
  };
}
