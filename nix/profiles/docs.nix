{ lib, pkgs, ... }:
{
  profiles.docs = {
    extends = [ "checks" ];
    module =
      { utils, ... }:
      {
        env = {
          D2_LAYOUT = "elk";
          D2_DARK_THEME = "200";
        };

        packages = with pkgs; [ d2 ];

        tasks = {
          "docs:build:diagrams" = utils.nuTask {
            description = "Render the D2 diagrams to SVG.";
            exec = ''
              for file in (glob docs/diagrams/**/*.d2) {
                  d2 $file ($file | path parse | update extension svg | path join)
              }
            '';
            execIfModified = utils.mkInputs [ "docs/diagrams/**/*.d2" ];
          };
          "docs:serve:site" = utils.nuTask {
            description = "Generate the diagrams, the navigation and the role table, then serve the docs.";
            packages =
              with pkgs;
              [
                lsof
                mkdocs
              ]
              ++ lib.optionals pkgs.stdenv.hostPlatform.isLinux [ pkgs.xdg-utils ];
            after = [ "docs:build:all" ];
            exec = "nu scripts/docs.nu serve --opener ${
              if pkgs.stdenv.hostPlatform.isDarwin then "open" else "xdg-open"
            }";
          };
          "docs:lint:diagrams" = utils.nuTask {
            description = "Check that the rendered diagrams are current.";
            exec = ''
              for file in (glob docs/diagrams/**/*.d2) {
                  let svg = $file | path parse | update extension svg | path join
                  let fresh = d2 $file - | complete

                  if $fresh.exit_code != 0 {
                      error make { msg: $fresh.stderr }
                  }

                  if ($fresh.stdout | str trim) != (open --raw $svg | str trim) {
                      error make { msg: $"($svg) is out of date: run docs:build:diagrams" }
                  }
              }
            '';
            execIfModified = utils.mkInputs (
              utils.mkGlobs "docs/diagrams" [
                "d2"
                "svg"
              ]
            );
          };
        };
      };
  };
}
