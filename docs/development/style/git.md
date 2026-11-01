# Git

Applies to: every commit.

## Commits

- **GIT-01** Use Conventional Commits: `type(scope)!: imperative summary`, lowercase, no trailing period. The scope is optional. [must, review]
- **GIT-02** Use only these types: `feat` (new user-visible capability), `fix` (user-visible defect), `refactor` (internal restructuring), `test` (test-only work), `docs` (documentation-only work) and `chore` (CI, tooling, dependencies and maintenance). [must, cog]
- **GIT-03** Don't use `perf` or `ci`. Classify release-worthy performance work as `feat` or `fix`, and classify CI work as `chore`. [must, cog]
- **GIT-04** Use `chore` for a change of tooling, even when it touches the chart folder. A `fix` or a `feat` releases the chart when `helm/` changed since the last release. [must, cog]
- **GIT-05** Make one logical change per commit. Squash fixups before merge. [should, review]

```text
✅ chore(nix): slim the devenv profiles
❌ ci: slim the devenv profiles
```

## Releases

- **GIT-06** Expect these release levels: a type with `!` bumps major, `feat:` bumps minor, `fix:` bumps patch, and all other types create no release. `cog` applies the rules, and `cog.toml` has its settings. [must, cog]
- **GIT-07** Use `!` only with `feat` or `fix` release commits. Explain the migration in the body. Don't use a `BREAKING CHANGE` footer. [must, cog]
- **GIT-08** Don't edit `CHANGELOG.md` by hand. The release step writes it from the commits, and it lists only breaking changes, features and fixes. [must, cog]

```text
feat(sync)!: require DBOS workflow recovery

Sync recovery now requires the DBOS worker and workflow state in PostgreSQL.

Migration:
- Deploy the DBOS worker before you enable scheduled syncs.
- Verify the DBOS system database is reachable.
```

## Hooks

- **GIT-09** Know that prek runs the hooks at commit time. If a hook rewrites a file, stage the file again and commit again. [must, review]
- **GIT-10** Know that `ripsecrets` blocks a line that looks like a secret. Add a whole file to `.secretsignore`, or mark one line with `pragma: allowlist secret`, as `helm/tests/ducklake_catalog_test.yaml` does. [must, ripsecrets]

<!-- nav:start -->
---

[Previous: Diagrams](docs-diagrams.md) | [Next: Proxy rocks](../rocks.md) | [Index](index.md) | [Home](../../index.md)
<!-- nav:end -->
