{ pkgs, utils, ... }:
let
  inherit (utils.credentials) storage;
  dataDir = utils.stateFile "seaweedfs";
  port = toString (utils.port "seaweedfs" "main");
in
{
  env = {
    TEST_S3_ENDPOINT = "127.0.0.1:${port}";
    TEST_S3_ACCESS_KEY = storage.admin.accessKey;
    TEST_S3_SECRET_KEY = storage.admin.secretKey;
  };

  files = utils.fileEntry "seaweedfs-identities.json" {
    json = {
      identities = [
        {
          name = "admin";
          credentials = [ storage.admin ];
          actions = [
            "Admin"
            "Read"
            "Write"
          ];
        }
        {
          name = "reader";
          credentials = [ storage.reader ];
          actions = [ "Read" ];
        }
      ];
    };
  };

  tasks."seaweedfs:clean" = utils.nuTask {
    description = "Empty the data folder of the object store.";
    exec = "rm --recursive --force ${dataDir}; mkdir ${dataDir}";
    before = [ (utils.processTask "seaweedfs") ];
  };

  processes.seaweedfs =
    utils.mkProcess {
      packages = with pkgs; [ seaweedfs ];
      command = [
        "weed"
        "server"
        "-ip=127.0.0.1"
        "-dir=${dataDir}"
        "-master.port=${toString (utils.port "seaweedfs" "master")}"
        "-volume.port=${toString (utils.port "seaweedfs" "volume")}"
        "-volume.max=8"
        "-filer"
        "-filer.port=${toString (utils.port "seaweedfs" "filer")}"
        "-s3"
        "-s3.port=${port}"
        "-s3.config=${utils.stateFile "seaweedfs-identities.json"}"
        "-s3.iam.readOnly=false"
      ];
    }
    // {
      ports = {
        main.allocate = 18333;
        master.allocate = 19333;
        volume.allocate = 18082;
        filer.allocate = 18888;
      };
      ready = utils.probes.anyAnswer "http://127.0.0.1:${port}";
    };
}
