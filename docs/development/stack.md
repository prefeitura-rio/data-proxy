# Run the console locally

To use the console in a browser, start the same stack without the tests:

```bash
devenv --profile console up
```

Run the console in the background with `devenv --profile console up -d`, wait with `devenv processes wait`, and stop it with `devenv down`. The command first runs the build of the console and shows its output. Then it starts the processes. The first start takes about one minute. A shell in the `console` profile runs the same build, and later runs skip it until a source changes.

The `console` profile starts no notebook site. To serve it, run `devenv --profile nb up -d`. That profile adds the build of the site and a server on port 13002, which the Query tab opens.

The console is at `http://console.localhost:13001/console`. To open that address in your browser, run `devenv --profile console tasks run console:open:browser` once the gateway is ready. The task uses `open` on macOS and `xdg-open` on Linux. If the port is busy, devenv uses the next free port and shows it in the process list.

Sign in as `admin` (role `dp_admin`, schema `test`, password `admin`). The tests use six more users with the password `web-test`:

- `web_admin`: role `dp_admin`, schema `test`.
- `web_viewer`: role `dp_console`.
- `web_api`: role `dp_api`, no console permission.
- `web_policy`: role `dp_policy`, the Access tab only.
- `web_other`: role `dp_admin`, the schema `other`.
- `web_root`: role `dp_admin`, every schema (`*`). The scenarios of `new-schema.feature` use it, because they add schemas with random names.

The seed adds 30 enqueued sync runs, two finished sync runs, 60 access policies, 15 errors and 32 history rows. It also adds two `test` tables with 5000 and 300 rows. The first finished sync run (`00000000-0000-4000-8000-0000000000f1`) has 61 tasks, 64 steps and 600 log lines. The second (`00000000-0000-4000-8000-0000000000f2`) has 5200 log lines, for the limit of 5000 lines.

No sync worker runs, so a sync run that you start stays `ENQUEUED`. The worker `control-worker` handles only the queues `control` and `schedules`, so cancel, resume, the schedule actions and the schedule reconcile work.

<!-- nav:start -->
---

[Previous: Frontend](frontend.md) | [Next: Nix setup](nix.md) | [Index](index.md) | [Home](../index.md)
<!-- nav:end -->
