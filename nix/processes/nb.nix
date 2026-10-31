{
  config,
  utils,
  ...
}:
let
  inherit (utils) urls;
in
{
  tasks = {
    "nb:build:site" =
      utils.mkTask {
        command = [
          "nu"
          "build.nu"
          "notebooks/query.py"
          "site"
        ];
      }
      // {
        description = "Build the notebook site.";
        cwd = "${config.devenv.root}/console/notebooks";
        before = [ (utils.processTask "nb") ];
        execIfModified = utils.mkInputs (
          [
            "console/notebooks/build.nu"
            "console/notebooks/bundle.ts"
            "console/notebooks/tsconfig.json"
            "console/notebooks/package.json"
            "console/notebooks/pnpm-lock.yaml"
            "console/notebooks/pnpm-workspace.yaml"
            "console/notebooks/pyproject.toml"
            "console/notebooks/uv.lock"
          ]
          ++ utils.mkGlobs "console/notebooks/src" [ "ts" ]
          ++ utils.mkGlobs "console/notebooks/notebooks" [ "py" ]
        );
      };
  };

  processes = {
    nb =
      utils.mkProcess {
        command = [
          "node"
          "server.ts"
        ];
        env = {
          PORT = utils.notebooksPort;
          CONSOLE_ORIGIN = urls.console;
          STORAGE_ORIGIN = urls.storage;
        };
      }
      // {
        cwd = "${config.devenv.root}/console/notebooks";
        ready = utils.probes.http {
          port = utils.notebooksPort;
          path = "/config.js";
        };
      };
  };
}
