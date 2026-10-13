{ lib }:
let
  quote = lib.escapeShellArg;

  mkPodmanProcess =
    {
      name,
      image ? null,
      build ? null,
      before ? [ ],
      env ? { },
      command ? [ ],
      args ? [ ],
      ready ? null,
      stopSignal ? 2,
      grace ? 20,
    }:
    assert lib.assertMsg (
      (image == null) != (build == null)
    ) "mkPodmanProcess ${name}: set image or build";
    let
      runtime = ''"''${CONTAINER_RUNTIME:-podman}"'';
      tag = "localhost/${name}:dev";
      context = build.context or null;
      buildLines = lib.optionals (build != null) (
        if context == null then
          [
            "context=$(mktemp -d)"
            "${runtime} build --file ${build.file} --tag ${tag} \"$context\""
            "rmdir \"$context\""
          ]
        else
          [ "${runtime} build --file ${build.file} --tag ${tag} ${quote context}" ]
      );
    in
    {
      exec = lib.concatStringsSep "\n" (
        buildLines
        ++ before
        ++ [
          "${runtime} rm -f ${name} >/dev/null 2>&1 || true"
          (
            "exec "
            + lib.concatStringsSep " " (
              [
                "${runtime} run --rm --network=host"
                "--name ${name}"
              ]
              ++ lib.mapAttrsToList (key: value: "-e ${quote "${key}=${value}"}") env
              ++ map quote args
              ++ [ (if build != null then tag else image) ]
              ++ map quote command
            )
          )
        ]
      );
      shutdown = {
        signal = stopSignal;
        inherit grace;
      };
    }
    // lib.optionalAttrs (ready != null) {
      ready = {
        exec = "${runtime} exec ${name} ${ready}";
        period = 1;
        failure_threshold = 300;
      };
    };
in
{
  inherit mkPodmanProcess;
}
