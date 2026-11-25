{ pkgs, ... }:
{
  profiles.default = {
    extends = [
      "console"
      "sync"
      "proxy"
      "provider"
      "chart"
      "docs"
      "tools"
    ];
    module = {
      languages = {
        go = {
          enable = true;
          lsp = {
            enable = true;
            package = pkgs.gopls;
          };
          delve.enable = true;
        };
        lua.lsp = {
          enable = true;
          package = pkgs.lua-language-server;
        };
        python.lsp = {
          enable = true;
          package = pkgs.pyrefly;
        };
        javascript.lsp.enable = true;
        typescript.lsp.enable = true;
        helm.lsp.enable = true;
      };

      env = {
        KUBECONFIG = "k3d/.kubeconfig";
      };

      packages = with pkgs; [
        kubecolor
        kubectl
        k6
        k3d
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
          packages = with pkgs; [ helmfile ];
        };
        token = {
          description = "Fetch an access token from a Keycloak realm.";
          package = pkgs.nushell;
          binary = "nu";
          exec = ../../scripts/token.nu;
        };
      };
    };
  };
}
