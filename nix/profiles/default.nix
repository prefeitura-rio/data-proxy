{ lib, pkgs, ... }:
{
  profiles.default = {
    extends = [
      "repo"
      "console"
      "sync"
      "proxy"
    ];
    module =
      { config, utils, ... }:
      {
        env = {
          KUBECONFIG = "k3d/.kubeconfig";
          D2_LAYOUT = "elk";
          D2_DARK_THEME = "200";
        };

        packages = with pkgs; [
          ast-grep
          kubecolor
          kubectl
          marksman
          k6
          k3d
          d2
          (google-cloud-sdk.withExtraComponents (
            with google-cloud-sdk.components; [ gke-gcloud-auth-plugin ]
          ))
        ];

        scripts = {
          cluster = {
            description = "Run the commands of the local cluster.";
            package = pkgs.nushell;
            binary = "nu";
            exec = ../../scripts/cluster.nu;
            packages = with pkgs; [
              git
              helmfile
            ];
          };
          token = {
            description = "Fetch an access token from a Keycloak realm.";
            package = pkgs.nushell;
            binary = "nu";
            exec = ../../scripts/token.nu;
          };
        };

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
