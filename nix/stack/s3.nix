{
  pkgs,
  data,
  utils,
  ...
}:
let
  inherit (data.credentials) storage;
  dataDir = utils.stateFile "s3";
  port = toString (data.port "s3" "main");
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

  tasks."s3:clean:data" = utils.nuTask {
    description = "Empty the data folder of the object store.";
    exec = "rm --recursive --force ${dataDir}; mkdir ${dataDir}";
    before = [ (utils.processTask "s3") ];
  };

  processes.s3 =
    utils.mkProcess {
      packages = with pkgs; [ seaweedfs ];
      command = [
        "weed"
        "server"
        "-ip=127.0.0.1"
        "-dir=${dataDir}"
        "-master.port=${toString (data.port "s3" "master")}"
        "-volume.port=${toString (data.port "s3" "volume")}"
        "-volume.max=8"
        "-filer"
        "-filer.port=${toString (data.port "s3" "filer")}"
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
