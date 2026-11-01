_:
let
  limitsMb = {
    base = 750;
    int = 400;
    release = 1000;
    console-unit = 900;
    proxy-unit = 1000;
    proxy = 1200;
    sync-unit = 900;
    sync = 1200;
    console = 2600;
    nb = 2600;
    repo = 2300;
    docs = 4300;
    default = 6500;
  };
in
{
  profiles.nix = {
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

        tasks."nix:lint:cmd" = utils.nuTask {
          description = "Check that every devenv command of the docs names a profile and a task that exist.";
          exec = "nu scripts/docs.nu cmd";
          execIfModified = utils.mkInputs (
            [
              "README.md"
              "STYLEGUIDE.md"
              "devenv.nix"
              "devenv.lock"
              "console/devenv.nix"
              "helm/devenv.nix"
              "proxy/devenv.nix"
              "sync/devenv.nix"
              "scripts/docs.nu"
            ]
            ++ utils.mkGlobs "nix" [ "nix" ]
            ++ utils.mkGlobs "docs" [
              "md"
              "svg"
              "d2"
              "json"
            ]
          );
        };
      };
  };
}
