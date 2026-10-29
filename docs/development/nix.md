# Nix setup

The root `devenv.yaml` composes the environment with `imports:`. Each component folder (`proxy/`, `sync/`, `console/` and `helm/`) has a `devenv.nix` with the profile of that component.

The profile names the modules of a test setup and the tests, and nothing else. `proxy/devenv.yaml` contains the inputs that only the proxy uses, and devenv merges them. `nix/profiles/` contains the profiles that belong to no component: `base`, `checks`, `default` and `python`. Run a setup from the root with `devenv --profile <name>`.

The `nix/` folder contains the shared parts. A module in `nix/processes/` or `nix/services/` contains one component. `nix/utils.nix` and `nix/data.nix` contain the helpers and the shared values. The `base` profile passes every module the argument `utils`. Every script is Nushell, except a bash script that's a list of plain commands, such as `enterTest`.

A long-running service is a process, and a command that runs and exits is a task. `utils.mkProcess` and `utils.mkTask` build the command and the variables, and the caller adds the other options with `//`. A task runs `before` a process that needs it, or `after` a process that it needs. A setup with several steps is a chain of tasks (`utils.mkTaskChain`), so a failure names the step:

```nix
processes.gateway =
  utils.mkProcess {
    command = [ (lib.getExe pkgs.caddy) "reverse-proxy" "--from" ":13001" "--to" "127.0.0.1:13004" ];
  }
  // {
    ready = utils.probes.http utils.urls.console;
    after = [ (utils.processTask "console") ];
  };
```

A tool belongs to the task, the process or the script that calls it. A task has no `packages` option in devenv, so `utils.nuTask`, `utils.mkTask`, `utils.mkProcess` and `utils.nushellCommand` take a `packages` list. They put the programs of the list first in `PATH` for that command only. A step of a chain takes its own `packages`. A script of `scripts.<name>` uses the `packages` option of devenv, which works the same way. A command that calls one program can name it with `lib.getExe`, as the example does.

The `packages` list of a profile contains only the tools that people type: `psql`, `goose`, `lx`, `cog`, `d2`, `kubectl` and a few more. A tool that only a task calls, such as `actionlint`, `mkdocs` or `rclone`, isn't there. The `languages.*` modules of devenv add their own tools to the shell, which devenv doesn't scope. To find a missing tool, run the task in a clean environment, for example `env -i HOME=$HOME PATH=<devenv and nix folders> devenv tasks run <task>`, because your own `PATH` can hide it.

devenv runs the processes with its native manager. A process or a task that names another one in `after` starts when that one is ready or has succeeded. `devenv test` starts the processes, waits for their `ready` checks and their setup tasks, and then runs the tests. `devenv up` starts the same processes without the tests. A module reads the port of another process with `utils.port "<process>" "<name>"`, and the shared logins and addresses come from `nix/data.nix`.

`execIfModified` skips a task when its input files didn't change, so delete `console/ui/build` or `console/notebooks/site` to force a build. The inputs are files and globs over files, because devenv compares a folder by its metadata and a fresh checkout changes every folder. See [Tests](tests.md#run-only-what-changed).

A file that Nix builds from values is a `files` entry. The entries are `sync/.env.test`, the SeaweedFS identities, the SQL that creates the databases, and the four nginx files. They live in the state folder of the run (`utils.fileEntry`), except `sync/.env.test`, which devenv links into the project and git ignores. A file whose input is YAML, such as the Keycloak realm and the sync settings, stays a derivation, because Nix can't read YAML. A task that reads a generated file runs `after` `devenv:files`, because `devenv tasks run` doesn't create files by itself.

<!-- nav:start -->
---

[Previous: Run the console locally](stack.md) | [Next: Database migrations](migrations.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
