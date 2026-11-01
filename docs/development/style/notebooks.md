# Notebooks

Applies to: `console/notebooks/**`.

- **NB-01** Never store a token, a cookie or a secret in a notebook. Get the data from the grant that the console sends, and read only the links in that grant. [must, review]
- **NB-02** Give every variable of a marimo cell a name that no other cell uses. A duplicate stops the whole notebook. [must, review]
- **NB-03** Check the origin and the source of every message in `console/notebooks/src/bridge.ts`, and send a message to one exact origin, never to `*`. [must, review]

See [TypeScript](typescript.md) for the rule about tests (TS-22).

<!-- nav:start -->
---

[Previous: Svelte](svelte.md) | [Next: Documentation](docs.md) | [Index](index.md) | [Home](../../index.md)
<!-- nav:end -->
