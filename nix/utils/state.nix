{ lib, config, ... }:
let
  inherit (import ../data.nix { inherit config; }) credentials port;

  /**
    The path of a file in the state folder of the test run.

    # Type

    ```
    stateFile :: String -> String
    ```
  */
  stateFile = name: "${config.devenv.state}/${name}";

  /**
    A `files` entry for a file in the state folder of the test run. Read the path of the file with `stateFile`.

    # Type

    ```
    fileEntry :: String -> AttrSet -> AttrSet
    ```
  */
  fileEntry = name: content: {
    ${lib.removePrefix "${config.devenv.root}/" (stateFile name)} = content;
  };

  /**
    The connection string of one database of the test server.

    # Inputs

    `database`
    : The name of the database.

    `user`, `password`
    : The login. They default to the administrator of the test server.

    `serverPort`
    : The port of the server. It defaults to the allocated port of the test server, which needs its process.

    # Type

    ```
    postgresUrl :: { database :: String, user :: String?, password :: String?, serverPort :: Int? } -> String
    ```
  */
  postgresUrl =
    {
      database,
      user ? credentials.postgres.user,
      password ? credentials.postgres.password,
      serverPort ? port "pg" "main",
    }:
    "postgresql://${user}:${password}@127.0.0.1:${toString serverPort}/${database}";
in
{
  inherit
    fileEntry
    postgresUrl
    stateFile
    ;
}
