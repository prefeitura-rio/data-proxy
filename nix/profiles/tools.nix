{ lib, pkgs, ... }:
let
  containerfiles = builtins.attrNames (
    lib.filterAttrs (name: _: lib.hasPrefix "Containerfile." name) (builtins.readDir ../..)
  );
in
{
  profiles.tools = {
    extends = [ "base" ];
    module =
      { utils, ... }:
      {
        packages = with pkgs; [
          cocogitto
          vale
        ];

        git-hooks.hooks = {
          ripsecrets.enable = true;
          tools-lint = utils.mkHook {
            profile = "tools";
            task = "tools:lint";
            files = "^(\\.github/.*\\.ya?ml|.*\\.(go|js|lua|nix|nu|py|sql|svelte|toml|tpl|ts)|Containerfile\\..*|migrations/SHA256SUMS|README\\.md|AGENTS\\.md|STYLEGUIDE\\.md)$";
          };
        };

        tasks = {
          "tools:lint:code" = utils.nuTask {
            description = "Check repository integrity and release metadata.";
            exec = "nu ($env.DEVENV_ROOT | path join scripts rules.nu)";
          };
          "tools:lint" = {
            description = "Run every tools lint task.";
            after = [
              "tools:lint:wf"
              "tools:lint:nu"
              "tools:lint:ctr"
              "tools:lint:code"
            ];
          };
          "tools:lint:wf" = utils.nuTask {
            description = "Lint the GitHub workflows.";
            packages = with pkgs; [
              actionlint
              shellcheck
            ];
            exec = "actionlint .github/workflows/*.yaml";
            execIfModified = utils.mkInputs (
              utils.mkGlobs ".github/workflows" [ "yaml" ] ++ utils.mkGlobs ".github/actions" [ "yaml" ]
            );
          };
          "tools:lint:nu" = utils.nuTask {
            description = "Lint the Nushell scripts.";
            packages = with pkgs; [ nu-lint ];
            exec = builtins.readFile ../../scripts/nu-lint.nu;
            execIfModified = utils.mkInputs [
              "helm/files/**/*.nu"
              "k3d/manifests/files/**/*.nu"
              "console/notebooks/**/*.nu"
              "scripts/**/*.nu"
              ".nu-lint.toml"
            ];
          };
          "tools:lint:ctr" = utils.nuTask {
            description = "Lint the Containerfiles.";
            packages = with pkgs; [ hadolint ];
            exec = lib.concatMapStringsSep "\n" (name: "hadolint ${name}") containerfiles;
            execIfModified = utils.mkInputs ([ ".hadolint.yaml" ] ++ containerfiles);
          };
        };

        enterTest = ''
          nu "$DEVENV_ROOT/scripts/tests/setup.nu"
          nu "$DEVENV_ROOT/scripts/tests/provider.nu"
          nu "$DEVENV_ROOT/scripts/tests/integrity.nu"
          nu "$DEVENV_ROOT/scripts/tests/checks.nu"
        '';
      };
  };
}
