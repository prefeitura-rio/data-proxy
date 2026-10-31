_:
let
  limitsMb = {
    base = 750;
    suites = 400;
    release = 1000;
    ui = 1250;
    proxy = 1200;
    sync = 1550;
    console = 2900;
    nb = 2900;
    checks = 2650;
    docs = 4700;
    default = 6500;
  };
in
{
  profiles.sizes = {
    extends = [ "base" ];
    module =
      { utils, ... }:
      {
        tasks."nix:lint:closure" = utils.nuTask {
          description = "Check that the closure of every profile is not larger than its limit in megabytes.";
          env.CLOSURE_LIMITS_MB = builtins.toJSON limitsMb;
          exec = "nu scripts/closure.nu";
          execIfModified = utils.mkInputs (
            [
              "devenv.nix"
              "devenv.yaml"
              "devenv.lock"
              "console/devenv.nix"
              "helm/devenv.nix"
              "proxy/devenv.nix"
              "sync/devenv.nix"
              "scripts/closure.nu"
            ]
            ++ utils.mkGlobs "nix" [ "nix" ]
          );
        };
      };
  };
}
