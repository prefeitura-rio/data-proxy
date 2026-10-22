{ pkgs, utils, ... }:
{
  tasks."sync:setup" =
    utils.mkTask {
      command = [
        "nu"
        "scripts/db.nu"
      ];
      packages = with pkgs; [ kubernetes-helm ];
    }
    // {
      description = "Create the databases of the sync tests.";
      after = [ (utils.processTask "postgres") ];
      wantedBy = [ (utils.processTask "postgres") ];
    };
}
