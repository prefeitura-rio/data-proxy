{ lib, pkgs, ... }:
{
  profiles.docs = {
    extends = [ "repo" ];
    module =
      { config, utils, ... }:
      {
        env = {
          D2_LAYOUT = "elk";
          D2_DARK_THEME = "200";
        };

        packages = with pkgs; [ d2 ];

        tasks = {
          "docs:build:diag" = utils.nuTask {
            description = "Render the D2 diagrams to SVG.";
            exec = "nu scripts/diag.nu build";
            execIfModified = utils.mkInputs [
              "docs/diagrams/**/*.d2"
              "scripts/diag.nu"
            ];
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
            after = lib.filter (lib.hasPrefix "docs:build:") (lib.attrNames config.tasks);
            exec = "nu scripts/docs.nu serve --opener ${
              if pkgs.stdenv.hostPlatform.isDarwin then "open" else "xdg-open"
            }";
          };
          "docs:lint:diag" = utils.nuTask {
            description = "Check that the rendered diagrams are current.";
            exec = "nu scripts/diag.nu check";
            execIfModified = utils.mkInputs (
              [ "scripts/diag.nu" ]
              ++ utils.mkGlobs "docs/diagrams" [
                "d2"
                "svg"
              ]
            );
          };
        };
      };
  };
}
