{ pkgs, utils, ... }:
{
  tasks."sync:setup:db" =
    utils.mkTask {
      command = [
        "nu"
        "scripts/db.nu"
      ];
      packages = with pkgs; [
        kubernetes-helm
        uv
      ];
    }
    // {
      description = "Create the databases of the sync tests.";
      after = [ (utils.processTask "pg") ];
      wantedBy = [ (utils.processTask "pg") ];
    };
}
