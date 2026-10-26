# Operations

These pages describe how to deploy, configure, scale, monitor and back up Data Proxy.

- [Helm Chart](helm.md) — Chart installation, images and versions.
- [Initialization](initialization.md) — Database storage, initialization and Jobs on demand.
- [Ingress](ingress.md) — The Istio gateway, the console and the notebooks.
- [Topology](topology.md) — Pods, replicas and the Kubernetes layout of the chart.
- [Hardening](hardening.md) — Pod security settings, network policies, console headers and image signing.
- [Environment Variables](environment.md) — Application defaults and Helm environment conversion.
- [Proxy variables](proxy.md) — The variables of the roles, of the proxy link and of the OpenResty proxy.
- [KEDA Scaling](keda.md) — The trigger, the replicas and the queue settings of the sync workers.
- [Metrics](metrics.md) — Worker metrics, proxy logs, load limits and scraping.
- [Backups](backups.md) — Encrypted `access_policy` backups and recovery.

<!-- nav:start -->
---

[Previous: E2E scenarios](../development/k6/scenarios.md) | [Next: Helm Chart](helm.md) | [Home](../index.md)
<!-- nav:end -->
