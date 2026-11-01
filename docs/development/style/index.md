# Style rules

Applies to: the whole repository.

These pages have the rules for the code and the docs of this repository. Read the page for the files that you change. [AGENTS.md](../../../AGENTS.md) maps each path to its page. The [style guide](../../../STYLEGUIDE.md) has the rules that matter most and the definition of done.

## The rule format

Each rule is one line: `- **PY-07** The rule. [must, ruff]`.

1. The ID has a prefix for the area and a number. Cite the ID in a review or a commit message.
2. `must` means the change can't merge when it breaks the rule. `should` means break it only with a reason.
3. The last word of the tag names what checks the rule: a tool, a devenv task or `review`.
4. A rule has at most 40 words, and a page has at most 900 words. `docs:lint:style` checks the format and that each cited ID exists.

## The pages

| Page | Prefix | Rules for |
| --- | --- | --- |
| [Principles](principles.md) | GEN | Every file, and the state of the system |
| [Logs](logs.md) | LOG | Every language that logs |
| [Tests](tests.md) | TEST | Every test |
| [k6 files](k6.md) | K6 | `k6/**` |
| [Python](python.md) | PY | `sync/**/*.py` |
| [Lua layout, tests and checks](lua-layout.md) | LUA | The proxy modules, the specs and the checks |
| [Lua code](lua-code.md) | LUA | The design and the errors of the Lua code |
| [Lua types and comments](lua-types.md) | LUA | The types, the schemas and the comments |
| [Nushell](nushell.md) | NU | `scripts/*.nu`, `helm/files/scripts/*.nu` |
| [Nix naming and code](nix.md) | NIX | The names of tasks, profiles and files, and the Nix code |
| [Nix layout, tasks and documentation](nix-layout.md) | NIX | The order of a file, the tasks and the doc comments |
| [SQL](sql.md) | SQL | Templates, migrations and permissions |
| [TypeScript](typescript.md) | TS | `console/ui/src/**/*.ts` and the browser tests |
| [Svelte](svelte.md) | SV | `console/ui/src/**/*.svelte` |
| [Notebooks](notebooks.md) | NB | `console/notebooks/**` |
| [Documentation](docs.md) | DOC | `docs/**`, `README.md` |
| [Diagrams](docs-diagrams.md) | DOC | `docs/diagrams/**` |
| [Git](git.md) | GIT | Commits, releases and hooks |

<!-- nav:start -->
---

[Previous: Checks](../checks.md) | [Next: Principles](principles.md) | [Index](../index.md) | [Home](../../index.md)
<!-- nav:end -->
