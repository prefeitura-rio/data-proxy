{ lib, config, ... }:
let
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
in
{
  inherit
    mkInputs
    mkGlobs
    ;
}
