# Style Guide

Rules for this repository. Follow an existing pattern before you add a new one.

The rules live in [one page for each area](docs/development/style/index.md). Read the page for the files that you change. [AGENTS.md](AGENTS.md) maps each path to its page.

## How to read a rule

Each rule has an ID, a level and a check: `**PY-07** The rule. [must, ruff]`. The ID is for a review or a commit message. `must` means the change can't merge when it breaks the rule. The last word of the tag names the tool, the devenv task or the review that checks it. See [the rule format](docs/development/style/index.md#the-rule-format).

## The rules that matter most

1. Read the code first, and follow an existing pattern (GEN-01, GEN-02).
2. Prefer deletion to a new abstraction, and keep one source of truth (GEN-03, GEN-06).
3. Fix the root cause. Fix a flaky test at its cause, and never add a retry (GEN-05, TEST-20).
4. Never put a user value into SQL text (SQL-01, SQL-02).
5. Never log a credential, a token, a connection string or a query value (LOG-03).
6. Put a test at the lowest level that proves the behavior, in table-driven cases with an `id` (TEST-01, TEST-14).
7. Write no comment inside a function body in Lua and TypeScript (LUA-81, TS-12).
8. Use no `any` and no type assertion in TypeScript, and no `Any` in Python (TS-06, PY-10).
9. Name a devenv task `<component>:<verb>:<target>` (NIX-01).
10. Write documents in sentences of 25 words or fewer, and commits as Conventional Commits (DOC-02, GIT-01, GIT-02).

## Definition of done

Run the checks inside `devenv` before you finish a change.

```bash
devenv --profile repo tasks run repo:lint repo:test:unit
devenv --profile int tasks run sync:test:int proxy:test:int console:test:int
devenv --profile nix tasks run nix:lint:closure nix:lint:cmd
```

- Run the second line for the components that you changed. It starts services.
- Run the third line after a change to a profile, a task, a package or a `devenv` command in a page.
- Never commit or push without explicit approval. Stage, summarize, and wait.

## Examples

Each page shows the rules that people break most as a pair, marked ✅ and ❌. For example, [Nix naming](docs/development/style/nix.md#naming) shows a task name that follows the grammar and one that doesn't, and [Git](docs/development/style/git.md#commits) shows the commit types.
