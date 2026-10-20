{ utils, ... }:
{
  tasks."sync:setup" =
    utils.mkTask {
      command = [
        "nu"
        "scripts/db.nu"
      ];
    }
    // {
      description = "Create the databases of the sync tests.";
      after = [ (utils.processTask "postgres") ];
      wantedBy = [ (utils.processTask "postgres") ];
    };
}
