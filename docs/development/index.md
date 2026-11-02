# Local Development

The local stack runs on k3d with Podman and Helm.

- [Setup](setup.md) — Prerequisites, the Python project and stopping the cluster.
- [Tests](tests.md) — The test suites and how to run them.
  - [Test stack](test-stack.md) — The processes, the ports and how to add a test container.
  - [Sync tests](sync-tests.md) — The unit and integration tests of the sync suite.
- [Checks](checks.md) — Development and release checks.
- [Proxy rocks](rocks.md) — The Lua libraries of the proxy.
- [Frontend](frontend.md) — The console and the notebooks.
- [Run the console locally](stack.md) — Start the console stack in a browser.
- [Nix setup](nix.md) — The Nix setup of the test stack.
- [Database migrations](migrations.md) — Database migrations.
- [Chart releases](releases.md) — Chart releases.
- [CI pipeline](ci.md) — Triggers and jobs.
  - [CI images and releases](ci-images.md) — Image tags, the pin step and the other `ci` commands.
  - [CI security and settings](ci-security.md) — SAST, branch protection, secrets and settings for another forge.
- [Seed BigQuery data](seed.md) — Seed data from BigQuery.
- [Access the API](api.md) — Access to the API.
- [k6 commands](k6/index.md) — The k6 commands, thresholds and the end-to-end layout.

<!-- nav:start -->
---

[Previous: Style](../style.md) | [Next: Setup](setup.md) | [Home](../index.md)
<!-- nav:end -->
