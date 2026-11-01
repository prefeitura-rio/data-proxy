{
  lib,
  pkgs,
  utils,
  ...
}:
let
  inherit (utils) bashPath toStrings;

  /**
    The name of the devenv task of a process, to use in `after`, `before` and `wantedBy`.

    # Type

    ```
    processTask :: String -> String
    ```
  */
  processTask = name: "devenv:processes:${name}";

  /**
    The command and the variables of a devenv process that runs a plain command for as long as the setup runs. A process
    that fails stays down. Add `cwd`, `after`, `ready` and the other options of the process with `//`.

    # Inputs

    `command`
    : The command, as a list of arguments.

    `env`
    : The variables to set.

    `packages`
    : The packages whose programs the command may call. They come first in `PATH` for this process only.

    # Type

    ```
    mkProcess :: { command :: [String], env :: AttrSet?, packages :: [Package]? } -> AttrSet
    ```
  */
  mkProcess =
    {
      command,
      env ? { },
      packages ? [ ],
    }:
    {
      exec = "${bashPath packages}exec ${lib.escapeShellArgs command}";
      env = toStrings env;
      restart.on = "never";
    };

  /**
    The command that runs an image with Podman in the network of the host. It replaces an old container of the same
    name.

    # Inputs

    `name`
    : The name of the container.

    `image`
    : The image.

    `env`
    : The variables of the container.

    `args`
    : More options of `podman run`.

    `command`
    : The command in the container.

    # Type

    ```
    podmanRun :: { name :: String, image :: String, env :: AttrSet?, args :: [String]?, command :: [String]? } -> [String]
    ```
  */
  podmanRun =
    {
      name,
      image,
      env ? { },
      args ? [ ],
      command ? [ ],
    }:
    [
      "podman"
      "run"
      "--rm"
      "--replace"
      "--network=host"
      "--name"
      name
    ]
    ++ lib.mapAttrsToList (key: value: "--env=${key}=${toString value}") env
    ++ args
    ++ [ image ]
    ++ command;

  probes =
    let
      mkProbe =
        ready:
        ready
        // {
          period = 1;
          failure_threshold = 1200;
        };
    in
    {
      /**
        A `ready` check that passes when the address answers with a success code. It runs every 2 seconds and fails
        after 600 tries, which is 20 minutes.
      */
      http =
        {
          port,
          path ? "/",
          host ? "127.0.0.1",
        }:
        mkProbe { http.get = { inherit port path host; }; };

      /**
        A `ready` check that passes with any answer. The storage answers 403 to a call without a key, which still shows
        that it runs. It uses the same timing as `http`.
      */
      anyAnswer =
        url:
        mkProbe { exec = "${lib.getExe pkgs.curl} --silent --output /dev/null ${lib.escapeShellArg url}"; };

      /**
        A `ready` check that passes when the command exits with 0. It uses the same timing as `http`.
      */
      command = command: mkProbe { exec = lib.escapeShellArgs command; };
    };
in
{
  inherit
    mkProcess
    podmanRun
    probes
    processTask
    ;
}
