{
  lib,
  pkgs,
  config,
}:
let
  toStrings = lib.mapAttrs (_: toString);

  inherit (import ./data.nix { inherit config; }) credentials port urls;

  /**
    The path of a file in the state folder of the test run.

    # Type

    ```
    stateFile :: String -> String
    ```
  */
  stateFile = name: "${config.devenv.state}/${name}";

  /**
    A `files` entry for a file in the state folder of the test run. Read the path of the file with `stateFile`.

    # Type

    ```
    fileEntry :: String -> AttrSet -> AttrSet
    ```
  */
  fileEntry = name: content: {
    ${lib.removePrefix "${config.devenv.root}/" (stateFile name)} = content;
  };

  /**
    The connection string of one database of the test server.

    # Inputs

    `database`
    : The name of the database.

    `user`, `password`
    : The login. They default to the administrator of the test server.

    # Type

    ```
    postgresUrl :: { database :: String, user :: String?, password :: String? } -> String
    ```
  */
  postgresUrl =
    {
      database,
      user ? credentials.postgres.user,
      password ? credentials.postgres.password,
    }:
    "postgresql://${user}:${password}@127.0.0.1:${toString (port "postgres" "main")}/${database}";

  /**
    A devenv task that runs Nushell.
  */
  nuTask = task: { package = pkgs.nushell; } // task;

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

    # Type

    ```
    mkProcess :: { command :: [String], env :: AttrSet? } -> AttrSet
    ```
  */
  mkProcess =
    {
      command,
      env ? { },
    }:
    {
      exec = "exec ${lib.escapeShellArgs command}";
      env = toStrings env;
      restart.on = "never";
      start.enable = config.devenv.isTesting;
    };

  /**
    The command of a devenv task that runs a plain command and exits. Add `cwd`, `after`, `before`, `wantedBy`,
    `execIfModified` and the other options of the task with `//`.

    # Inputs

    `command`
    : The command, as a list of arguments.

    `env`
    : The variables to set.

    # Type

    ```
    mkTask :: { command :: [String], env :: AttrSet? } -> AttrSet
    ```
  */
  mkTask =
    {
      command,
      env ? { },
    }:
    {
      exec = lib.escapeShellArgs command;
      env = toStrings env;
    };

  /**
    A chain of tasks named `<name>:<step>`.

    Each step runs after the step before it. The first step runs after `after`, and the last step runs before `before`.

    # Inputs

    `name`
    : The prefix of the task names.

    `steps`
    : A list of `{ name, command }`, and an optional `description`.

    `env`
    : The variables of every step.

    `common`
    : The options that every step shares, such as `cwd` and `execIfModified`.

    `after`, `before`
    : The tasks and processes around the chain.

    # Type

    ```
    mkTaskChain :: { name :: String, steps :: [{ name :: String, command :: [String] }], env :: AttrSet?, common :: AttrSet?, after :: [String]?, before :: [String]? } -> { tasks :: AttrSet, last :: String }
    ```

    `last` is the name of the last step.
  */
  mkTaskChain =
    {
      name,
      steps,
      env ? { },
      common ? { },
      after ? [ ],
      before ? [ ],
    }:
    let
      names = map (step: "${name}:${step.name}") steps;
      afters = [ after ] ++ map (previous: [ previous ]) names;
      befores = map (_: [ ]) (lib.init steps) ++ [ before ];
      order = lib.zipListsWith (stepAfter: stepBefore: {
        after = stepAfter;
        before = stepBefore;
      }) afters befores;
    in
    {
      tasks = lib.listToAttrs (
        lib.zipListsWith (
          step: stepOrder:
          lib.nameValuePair "${name}:${step.name}" (
            mkTask {
              inherit (step) command;
              inherit env;
            }
            // common
            // stepOrder
            // lib.optionalAttrs (step ? description) { inherit (step) description; }
          )
        ) steps order
      );
      last = lib.last names;
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
          period = 2;
          failure_threshold = 600;
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
      anyAnswer = url: mkProbe { exec = "curl --silent --output /dev/null ${lib.escapeShellArg url}"; };

      /**
        A `ready` check that passes when the command exits with 0. It uses the same timing as `http`.
      */
      command = command: mkProbe { exec = lib.escapeShellArgs command; };
    };

  /**
    A command of the shell of the project that runs one script of `scripts/`. The copy has the plain name of the
    command, so that the help of the script shows it and not the name of a store path.

    # Type

    ```
    nushellCommand :: String -> Derivation
    ```
  */
  nushellCommand =
    name:
    let
      script = pkgs.runCommand name { } ''
        mkdir -p $out
        cp ${../scripts + "/${name}.nu"} $out/${name}
      '';
    in
    pkgs.writers.writeNuBin name ''
      def --wrapped main [...args: string] {
          exec ${lib.getExe pkgs.nushell} --include-path ${config.devenv.root}/scripts ${script}/${name} ...$args
      }
    '';

  /**
    Apply a definition only in `devenv test`.
  */
  whenTesting = lib.mkIf config.devenv.isTesting;
in
{
  inherit
    credentials
    fileEntry
    mkProcess
    mkTask
    mkTaskChain
    processTask
    nushellCommand
    nuTask
    podmanRun
    port
    postgresUrl
    probes
    stateFile
    urls
    whenTesting
    ;
}
