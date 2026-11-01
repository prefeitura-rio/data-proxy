# Nix naming and code

Applies to: `**/*.nix`, `devenv.yaml`.

Nix files build the test setup of the devenv profiles. The profile of a component lives in `<component>/devenv.nix`, and the root `devenv.yaml` imports it. `nix/profiles/` contains the profiles that belong to no component. `nix/stack/` contains one part of the test stack for each file, and `nix/utils/` contains the helpers.

## Naming

- **NIX-01** Name a task `<component>:<verb>:<target>`. [must, devenv]
- **NIX-02** Use one of these verbs: `install`, `build`, `lint`, `test`, `setup`, `fetch`, `clean`, `serve` or `open`. [must, devenv]
- **NIX-03** Name the target for the object of the verb, in the singular, such as `py`, `image` or `db`. For `test`, the target is the task type: `unit` or `int`. [must, review]
- **NIX-04** Name a task that groups other tasks with the component `repo`: `repo:lint` and `repo:test:unit`. `devenv tasks run repo` runs both. [must, devenv]
- **NIX-05** Run a group with a namespace prefix. `devenv tasks run sync:test` runs the sync tests of the active profile. [should, review]
- **NIX-06** Choose the component from three kinds. A deployable is `sync`, `proxy`, `console` or `chart`. A part of the test stack is `pg`, `pgrest`, `kc`, `s3` or `nb`. A repository tool is `nu`, `ci`, `ctr`, `docs`, `nix` or `repo`. [must, devenv]
- **NIX-07** Abbreviate a name that's long. Use the same short form in every layer: the task, the process, the profile and the test image. A file name keeps the full word. [must, review]
- **NIX-08** Use these short forms: PostgreSQL `pg`, PostgREST `pgrest`, Keycloak `kc`, SeaweedFS `s3`, notebooks `nb`, containers `ctr`, workflows `wf`, diagrams `diag`, provision `prov` and configure `conf`. [must, review]
- **NIX-09** Add a word to this list before you use a new short form. [must, review]
- **NIX-10** Keep the full words in environment variables and in the names of registry images, because the tests and the chart read them. [must, review]
- **NIX-11** Name a profile `<component>` for the whole stack of a component and `<component>-unit` for the part that needs no service. [must, review]
- **NIX-12** Name a profile by its job. `shared.nix` has `base` and `python`, which the others use. `repo` runs lint and unit tasks, and `integration` has the integration suites. `nix` checks the profiles, and `default` is the shell of a developer. [must, review]
- **NIX-13** Give a file in `nix/stack/` or `nix/profiles/` the full name of its part or profile, such as `postgres.nix` or `integration.nix`. [must, review]
- **NIX-14** Put a component profile in the folder of the component, and list the folder under `imports:` in `devenv.yaml`. Put an input that only that component uses in the `devenv.yaml` of the folder. [must, review]
- **NIX-15** Import `nix/profiles/*` in the root `devenv.nix`. [must, review]

devenv rejects a task name that breaks these rules when it evaluates a profile. The verbs and the components are in `nix/utils/naming.nix`.

```text
✅ sync:test:int        console:setup:db      repo:lint
❌ sync:tests:int       console:db            lint:all
```

## Code

- **NIX-16** Format with `nixfmt`. Don't align by hand. [must, nixfmt]
- **NIX-17** Put every web address in quotes. Don't use a bare address. [must, review]
- **NIX-18** Don't use `rec`. Bind the shared names in a `let` block and use `inherit`. [must, code]
- **NIX-19** Don't use `with` at the top of a file. Use `with pkgs;` for a list of packages, or for one expression that uses three or more `pkgs` attributes, as `python.nix` does. [must, review]
```nix
✅ packages = with pkgs; [ curl goose nushell ];
✅ package = with pkgs; symlinkJoin { name = "uv"; paths = [ uv ]; nativeBuildInputs = [ makeWrapper ]; };
❌ with import <nixpkgs> { };
```

- **NIX-20** Don't use lookup paths such as `<nixpkgs>`. The lock file of devenv pins every input. [must, code]
- **NIX-21** Name the arguments of a function in a set pattern, and give an optional argument a default with `?`. [must, review]
- **NIX-22** Use `inherit (set) name;` instead of `name = set.name;`. [should, review]
- **NIX-23** Write a path that Nix must track as a path literal, such as `../../Containerfile.pg`. A string path breaks in the sandbox. [must, review]
- **NIX-24** Keep a value in one place. A login and an address live in `nix/data.nix`, and a port lives on its process. [must, review]
- **NIX-25** Declare the port of a process with `ports.<name>.allocate = <base>`, and read it with `data.port "<process>" "<name>"`. A devenv service sets its `port` option, as `services.redis` does. Don't write a port number in another place. [must, review]
- **NIX-26** Take `utils` and `data` from the module arguments. Don't import `nix/utils/` or `nix/data.nix` in a module. [must, review]
- **NIX-27** Use bash only for a script that's a list of plain commands, with no loop, condition or variable, such as `scripts.seed` or an `enterTest`. Write anything else in Nushell. [must, review]
- **NIX-28** Prefer a plain command with `env` and `cwd` to a generated wrapper script. [should, review]
- **NIX-29** Give every task and every script a `description`. [must, review]

<!-- nav:start -->
---

[Previous: Nushell](nushell.md) | [Next: Nix layout, tasks and documentation](nix-layout.md) | [Index](index.md) | [Home](../../index.md)
<!-- nav:end -->
