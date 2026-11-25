{
  lib,
  pkgs,
  utils,
  ...
}:
let
  inherit (utils)
    bashPath
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

in
{
  inherit
    mkTask
    nuTask
    ;
}
