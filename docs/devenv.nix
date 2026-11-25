{
  lib,
  pkgs,
  ...
}:
{
  profiles.docs = {
    extends = [ "base" ];
    module =
      {
        config,
        data,
        utils,
        ...
      }:
      {
        packages = with pkgs; [
          vale
          mdbook
          mdbook-mermaid
          lychee
          repomix
        ];

        git-hooks.hooks.vale = {
          enable = true;
          name = "vale";
          entry = toString (
            pkgs.writeShellScript "vale-hook" ''
              set -e
              ${lib.getExe pkgs.vale} sync --no-exit
              exec ${lib.getExe pkgs.vale} --no-exit "$@"
            ''
          );
          files = "^(sync/src/.*\\.py|scripts/.*\\.nu|helm/files/scripts/.*\\.nu)$";
          require_serial = true;
        };
        git-hooks.hooks.docs-lint = utils.mkHook {
          profile = "docs";
          task = "docs:lint";
          files = "^(docs/.*|README\\.md|AGENTS\\.md|STYLEGUIDE\\.md|book\\.toml|lychee\\.toml|repomix\\.config\\.json|helm/files/config/rbac\\.toml)$";
        };

        processes.docs =
          utils.mkProcess {
            command = [
              "mdbook"
              "serve"
              "--hostname"
              "127.0.0.1"
              "--port"
              (toString (data.port "docs" "main"))
            ];
          }
          // {
            cwd = config.devenv.root;
            ports.main.allocate = 18200;
            ready = utils.probes.http { port = data.port "docs" "main"; };
          };

        tasks = {
          "docs:build:model" =
            utils.mkTask {
              command = [
                "devenv"
                "--profile"
                "model"
                "test"
              ];
            }
            // {
              description = "Start the databases, write two diagrams of owned tables, and stop the databases.";
              execIfModified = utils.mkInputs [
                "migrations/*/*.sql"
                "helm/files/templates/postgres/*.sql"
                "sync/uv.lock"
                "docs/.tbls.yaml"
                "scripts/db.nu"
                "console/devenv.nix"
                "nix/services/postgres.nix"
              ];
            };
          "docs:build:mermaid" = {
            description = "Install the Mermaid scripts for the book.";
            before = [ (utils.processTask "docs") ];
            exec = "mdbook-mermaid install .";
          };
          "docs:build:site" = {
            description = "Build the HTML, Markdown and llms.txt bundle in .book/html.";
            after = [ "docs:build:mermaid" ];
            exec = ''
              rm --recursive --force .book
              mdbook build
              cp docs/SUMMARY.md .book/markdown/SUMMARY.md
              repomix .book/markdown --config repomix.config.json --output .book/html/llms.txt --quiet
              cp --recursive .book/markdown/. .book/html/
              rm --force .book/html/404.html .book/html/devenv.nix
            '';
          };
          "docs:lint:prose" = utils.nuTask {
            description = "Check documentation prose with Vale.";
            packages = with pkgs; [ vale ];
            exec = "vale sync --no-exit\nvale --no-exit docs README.md AGENTS.md STYLEGUIDE.md";
            execIfModified = utils.mkInputs (
              [
                ".vale.ini"
                ".vale/styles/DataProxy/*.yml"
                ".vale/styles/config/vocabularies/data-proxy/*.txt"
                "README.md"
                "AGENTS.md"
                "STYLEGUIDE.md"
              ]
              ++ utils.mkGlobs "docs" [
                "js"
                "json"
                "md"
                "mmd"
                "nix"
                "yaml"
              ]
            );
          };
          "docs:lint:book" = {
            description = "Build the book and check file and heading links with Lychee.";
            after = [ "docs:build:site" ];
            exec = ''
              lychee --config lychee.toml --root-dir "$PWD/.book/html" .book/html
              lychee --config lychee.toml README.md AGENTS.md STYLEGUIDE.md
            '';
          };
        };
      };
  };
}
