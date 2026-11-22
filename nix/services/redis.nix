{ pkgs, ... }:
{
  services.redis = {
    enable = true;
    package = pkgs.valkey;
    port = 18379;
    extraConfig = ''
      locale-collate C
      save ""
    '';
  };
}
