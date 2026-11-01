{
  lib,
  pkgs,
  utils,
  ...
}:
let
  inherit (utils)
    bashPath
    mkInputs
    nuPath
    toStrings
    ;

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
in
{
  inherit
    mkTask
    mkTaskChain
    nuTask
    serviceSuite
    ;
}
