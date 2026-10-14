{
  pkgs,
  config,
  ...
}:
{
  profiles.console = {
    extends = [ "base" ];
    module = {
      packages = [ pkgs.goose ];

      languages.javascript = {
        enable = true;
        pnpm.enable = true;
      };

      languages.typescript.enable = true;

      scripts.migrate.exec = ''nu ${config.devenv.root}/scripts/migrate.nu "$@"'';

      tasks."lint:console" = {
        cwd = "console";
        exec = ''
          pnpm install --frozen-lockfile
          pnpm check
        '';
        execIfModified = [
          "console/src"
          "console/package.json"
          "console/pnpm-lock.yaml"
          "console/tsconfig.json"
          "console/vite.config.ts"
        ];
      };
    };
  };
}
