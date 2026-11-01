{ lib, ... }:
let
  toStrings = lib.mapAttrs (_: toString);

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
in
{
  inherit
    bashPath
    nuPath
    toStrings
    ;
}
