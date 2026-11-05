{ lib, ... }:
let
  verbs = [
    "install"
    "build"
    "lint"
    "test"
    "setup"
    "fetch"
    "clean"
    "open"
  ];

  components = [
    "repo"
    "sync"
    "proxy"
    "console"
    "chart"
    "tf"
    "pg"
    "pgrest"
    "kc"
    "s3"
    "nb"
    "nu"
    "ci"
    "ctr"
    "docs"
    "nix"
  ];

  isValidTaskName =
    name:
    let
      parts = lib.splitString ":" name;
      verb = lib.elemAt parts 1;
    in
    lib.hasPrefix "devenv:" name
    || (lib.length parts == 2 && lib.head parts == "repo" && lib.elem verb verbs)
    || (
      lib.length parts == 3
      && lib.elem (lib.head parts) components
      && lib.elem verb verbs
      && builtins.match "[a-z0-9-]+" (lib.elemAt parts 2) != null
    );

  /**
    The task names that break the naming rules of the style guide: `<component>:<verb>:<target>`, a `repo:<verb>` group, or
    a task of devenv. The names come from the glossary in `verbs` and `components`.

    # Type

    ```
    badTaskNames :: [String] -> [String]
    ```
  */
  badTaskNames = names: lib.filter (name: !isValidTaskName name) names;
in
{
  inherit
    badTaskNames
    ;
}
