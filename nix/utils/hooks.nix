_:
let
  /**
    A git hook that runs one devenv task. The hook runs at commit time on a commit that touches a file of `files`, and
    `devenv test` runs it with the other hooks, at the same time, before `enterTest`. A hook never gets the file names,
    because the task checks the whole component.

    # Inputs

    `profile`
    : The profile that holds the task.

    `task`
    : The name of the task, or a prefix that selects a group of tasks.

    `files`
    : The pattern of the files that start the hook. A commit that changes no matching file skips the hook.

    # Type

    ```
    mkHook :: { profile :: String, task :: String, files :: String } -> AttrSet
    ```
  */
  mkHook =
    {
      profile,
      task,
      files,
    }:
    {
      enable = true;
      name = task;
      entry = "devenv --profile ${profile} tasks run ${task}";
      language = "system";
      pass_filenames = false;
      priority = 0;
      inherit files;
    };
in
{
  inherit mkHook;
}
