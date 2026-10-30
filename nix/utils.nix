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
    The lines of a Bash command that put the programs of some packages first in `PATH`. They change `PATH` for that
    command only. The result is empty without packages.

    # Type

    ```
    bashPath :: [Package] -> String
    ```
  */
  bashPath =
    packages:
    lib.optionalString (packages != [ ]) "export PATH=${lib.makeBinPath packages}:\"$PATH\"\n";

  /**
    The lines of a Nushell command that put the programs of some packages first in `PATH`. They change `PATH` for that
    command only. The result is empty without packages.

    # Type

    ```
    nuPath :: [Package] -> String
    ```
  */
  nuPath =
    packages:
    lib.optionalString (packages != [ ])
      "$env.PATH = ($env.PATH | prepend ${builtins.toJSON (lib.splitString ":" (lib.makeBinPath packages))})\n";

  /**
    A devenv task that runs Nushell. It puts the programs of `packages` first in `PATH` for the task only. Add the other
    options of the task with the same attribute set.

    # Type

    ```
    nuTask :: { packages :: [Package]?, exec :: String? } -> AttrSet
    ```
  */
  nuTask =
    {
      packages ? [ ],
      exec ? null,
      ...
    }@task:
    {
      package = pkgs.nushell;
    }
    // removeAttrs task [ "packages" ]
    // lib.optionalAttrs (exec != null) { exec = nuPath packages + exec; };

  /**
    The input files of a task, for `execIfModified`. Every entry must name files: a file, or a glob whose last part names
    an extension. The call throws for a directory, for a file that does not exist, for a glob over a folder that does not
    exist, and for a glob that ends in a star.

    devenv compares a file by its content, so a fresh checkout does not run the task again. It compares a directory by
    its metadata, and a glob that ends in two stars matches the directories too. Every directory is new after a
    checkout, so the task would run on every CI run. A glob does not match a hidden folder such as `.venv`.

    # Type

    ```
    mkInputs :: [String] -> [String]
    ```
  */
  mkInputs =
    entries:
    map (
      entry:
      let
        path = "${config.devenv.root}/${entry}";
        parts = lib.splitString "/" entry;
        last = lib.last parts;
        names = last != "*" && last != "**";
        isGlob = lib.hasInfix "*" entry;
        literal = lib.optionalString (globBase != null) (builtins.head globBase);
        globBase = builtins.match "([^*]*)/[^/]*[*].*" entry;
        found =
          if isGlob then
            builtins.pathExists "${config.devenv.root}/${literal}"
          else
            builtins.pathExists path && builtins.readFileType path == "regular";
      in
      if names && found then
        entry
      else
        throw "execIfModified lists ${entry}. It must be a file that exists, or a glob over files, such as dir/**/*.ext, in a folder that exists."
    ) entries;

  /**
    The globs of the files under a folder, one for each extension. The call throws if the folder has a file with another
    extension, so a new kind of file cannot stay outside the inputs of a task. It skips the hidden folders,
    `__pycache__`, `node_modules` and `charts` (the downloaded dependencies of a Helm chart).

    # Type

    ```
    mkGlobs :: String -> [String] -> [String]
    ```
  */
  mkGlobs =
    dir: extensions:
    let
      skipped =
        name:
        lib.hasPrefix "." name
        || lib.elem name [
          "__pycache__"
          "node_modules"
          "charts"
        ];
      walk =
        path:
        lib.concatLists (
          lib.mapAttrsToList (
            name: type:
            if skipped name then
              [ ]
            else if type == "directory" then
              walk "${path}/${name}"
            else
              [ "${path}/${name}" ]
          ) (builtins.readDir path)
        );
      extensionOf = file: lib.last (lib.splitString "." (baseNameOf file));
      unknown = lib.filter (file: !(lib.elem (extensionOf file) extensions)) (
        walk "${config.devenv.root}/${dir}"
      );
    in
    if unknown != [ ] then
      throw "The folder ${dir} holds ${builtins.head unknown}, and its extension is not in ${builtins.toJSON extensions}. Add the extension, so a change of the file runs the tasks."
    else
      map (extension: "${dir}/**/*.${extension}") extensions;

  /**
    A devenv task that runs the tests of a profile with `devenv test`, which starts the processes of the profile, runs its
    `enterTest` and stops the processes. The task runs again only after a change of one of its inputs, and a failed run
    leaves no state, so it runs again too. The task belongs to another profile than `profile`: the profile of a task
    keeps its state in its own folder, and a task that runs its own profile would wait for that state.

    # Inputs

    `profile`
    : The profile that holds the tests.

    `inputs`
    : The files and the globs that the tests and their servers read.

    `description`
    : What the task runs.

    # Type

    ```
    serviceSuite :: { profile :: String, inputs :: [String], description :: String } -> AttrSet
    ```
  */
  serviceSuite =
    {
      profile,
      inputs,
      description,
    }:
    mkTask {
      command = [
        "devenv"
        "--profile"
        profile
        "test"
      ];
    }
    // {
      inherit description;
      execIfModified = mkInputs inputs;
    };

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
    The command of a devenv task that runs a plain command and exits. Add `cwd`, `after`, `before`, `wantedBy`,
    `execIfModified` and the other options of the task with `//`.

    # Inputs

    `command`
    : The command, as a list of arguments.

    `env`
    : The variables to set.

    `packages`
    : The packages whose programs the command may call. They come first in `PATH` for this task only.

    # Type

    ```
    mkTask :: { command :: [String], env :: AttrSet?, packages :: [Package]? } -> AttrSet
    ```
  */
  mkTask =
    {
      command,
      env ? { },
      packages ? [ ],
    }:
    {
      exec = bashPath packages + lib.escapeShellArgs command;
      env = toStrings env;
    };

  /**
    A chain of tasks named `<name>:<step>`.

    Each step runs after the step before it. The first step runs after `after`, and the last step runs before `before`.

    # Inputs

    `name`
    : The prefix of the task names.

    `steps`
    : A list of `{ name, command }`, with an optional `description` and an optional `packages` list for that step.

    `env`
    : The variables of every step.

    `packages`
    : The packages whose programs the steps may call.

    `common`
    : The options that every step shares, such as `cwd` and `execIfModified`.

    `after`, `before`
    : The tasks and processes around the chain.

    # Type

    ```
    mkTaskChain :: { name :: String, steps :: [{ name :: String, command :: [String] }], env :: AttrSet?, packages :: [Package]?, common :: AttrSet?, after :: [String]?, before :: [String]? } -> { tasks :: AttrSet, last :: String }
    ```

    `last` is the name of the last step.
  */
  mkTaskChain =
    {
      name,
      steps,
      env ? { },
      packages ? [ ],
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
              packages = packages ++ step.packages or [ ];
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
      anyAnswer =
        url:
        mkProbe { exec = "${lib.getExe pkgs.curl} --silent --output /dev/null ${lib.escapeShellArg url}"; };

      /**
        A `ready` check that passes when the command exits with 0. It uses the same timing as `http`.
      */
      command = command: mkProbe { exec = lib.escapeShellArgs command; };
    };

  /**
    A command of the shell of the project that runs one script of `scripts/`. The copy has the plain name of the
    command, so that the help of the script shows it and not the name of a store path. The programs of `packages` come
    first in `PATH` while the script runs.

    # Type

    ```
    nushellCommand :: String -> [Package] -> Derivation
    ```
  */
  nushellCommand =
    name: packages:
    let
      script = pkgs.runCommand name { } ''
        mkdir -p $out
        cp ${../scripts + "/${name}.nu"} $out/${name}
      '';
    in
    pkgs.writers.writeNuBin name ''
      def --wrapped main [...args: string] {
          ${nuPath packages}exec ${lib.getExe pkgs.nushell} --include-path ${config.devenv.root}/scripts ${script}/${name} ...$args
      }
    '';
in
{
  inherit
    credentials
    fileEntry
    mkGlobs
    mkInputs
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
    serviceSuite
    stateFile
    urls
    ;
}
