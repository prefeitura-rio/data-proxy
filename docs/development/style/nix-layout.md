# Nix layout, tasks and documentation

Applies to: `**/*.nix`.

## Layout of a file

Read each file from the top to the bottom. The main part of the file is the last block.

1. The argument set, then `utils`.
2. Constants that the file uses more than once.
3. Helper functions, each after the values it uses.
4. Parts that the main block uses more than once, such as a `mkTaskChain` result.
5. The main block: `profiles.<name>`, `processes`, or `services`.

- **NIX-30** Bind a name in `let` only if the file uses it at least twice. Write a value that the file uses once where it's used. [must, review]
- **NIX-31** In a profile `module`, keep this order: `imports`, `env`, `packages`, `languages`, `scripts`, `git-hooks`, `tasks`, `enterTest`. [must, review]
- **NIX-32** In `tasks`, put the group tasks (`repo:lint`, `repo:test:unit`) last. [must, review]
- **NIX-33** Always declare processes and tasks. Don't gate them on `config.devenv.isTesting`, so `devenv up` and `devenv test` run the same stack. [must, review]
- **NIX-34** Gate only `console:open:browser`, because it would open a browser and hang `devenv test`. [must, review]
- **NIX-35** Don't give a local function the name of a helper in `nix/utils/`. Name it for what it builds, such as `mkNginx`. [must, review]
- **NIX-54** Put a helper in the file of its topic in `nix/utils/`. The file takes `{ lib, pkgs, config, utils, ... }` and returns its helpers, and `import-tree` adds it to `utils`. [must, review]

## Processes and tasks

- **NIX-36** Make a service that stays up a `processes` entry. Make a command that runs and exits a `tasks` entry. Don't run a one-shot command as a process. [must, review]
- **NIX-37** Use `utils.mkTask` for one task and `utils.mkTaskChain` for steps in order. Add the other options of a process or task with `//`. [should, review]
- **NIX-38** Let a task that a process needs use `before`. Let a task that needs a running service use `after` and `wantedBy` with `utils.processTask "<name>"`. [must, review]
- **NIX-39** Define the `<component>:test:int` tasks in `nix/profiles/integration.nix`, so the job that runs one fetches no tool of another profile. [must, review]
- **NIX-40** Define a `<component>:test:unit` task in the unit profile of its component. [must, review]
- **NIX-41** Use `execIfModified` on a build task, on a lint or unit task, and on a suite that starts services, with the input files from `utils.mkInputs`. [must, review]
- **NIX-42** List files and globs that end in an extension, never a folder or `dir/**`. Use `utils.mkGlobs` for the files of a folder. [must, devenv]
- **NIX-43** Build a file from variables with the `files` option, and put it in the state folder with `utils.fileEntry`. Use a derivation only when the input is YAML or another format that Nix can't read. [must, review]
- **NIX-44** Give a task that reads a generated file `after = [ "devenv:files" ]`. [must, review]
- **NIX-45** Keep each devenv profile under its limit in `nix/profiles/nix.nix`. Run `nix:lint:closure` after you add a package to a profile, a task or a process. [must, devenv]

## Documentation of a function

- **NIX-46** Write the documentation of a function as an [RFC 145](https://github.com/NixOS/rfcs/blob/master/rfcs/0145-doc-strings.md) doc comment. Start it with `/**` and put it directly before the function. [must, review]
- **NIX-47** Document functions only. Don't document a variable, a set of values, or a module. [must, review]
- **NIX-48** Write the content as CommonMark. Start with one sentence that says what the function returns. [must, review]
- **NIX-49** Use these headings when they apply: `# Inputs` with one term for each argument, and `# Type` with a type signature in a code block. [must, review]
- **NIX-50** Write the type as `name :: { key :: Type, optional :: Type? } -> Result`. [must, review]
- **NIX-51** Don't add a comment that explains how code works. Name the value or split the function instead. [must, review]
- **NIX-52** Document each function of a set where it's defined, as `probes` does. [should, review]

````nix
/**
  A `ready` check that passes when the command exits with 0.

  # Inputs

  `command`
  : The command, as a list of arguments.

  # Type

  ```
  command :: [String] -> AttrSet
  ```
*/
command = command: mkProbe (lib.escapeShellArgs command);
````

## Tests

- **NIX-53** Run `devenv --profile integration tasks run <component>:test:int` for each component that a change touches. The first run can fail when a hook formats a file. Run it again. [must, review]

<!-- nav:start -->
---

[Previous: Nix naming and code](nix.md) | [Next: SQL](sql.md) | [Index](index.md) | [Home](../../index.md)
<!-- nav:end -->
