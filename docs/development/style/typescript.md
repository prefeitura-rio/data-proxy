# TypeScript

Applies to: `console/ui/src/**/*.ts`, `console/ui/tests/**`.

The console is a SvelteKit app in `console/ui/`. The sources of the rules are the [Svelte best practices](https://svelte.dev/docs/svelte/best-practices), the [typescript-eslint configurations](https://typescript-eslint.io/users/configs) and the [Google TypeScript guide](https://google.github.io/styleguide/tsguide.html). ESLint and `svelte-check` enforce the rules, so a rule that a tool can't check is the exception.

## Architecture

- **TS-01** Never call PostgREST from the browser. A `load` function or a form action in `console/ui/src/routes` calls it through `lib/server/postgrest.ts`, with the token of the session. [must, review]
- **TS-02** Keep the token and every secret in `lib/server/`. [must, review]
- **TS-03** Keep the logic that needs a test in a module with no import of `$app` or `$env`, so that Vitest runs it. Pass the settings and `fetch` as arguments. [must, review]
- **TS-04** Map a status to a message in one place (`postgrest.ts` and `messages.ts`), so that a page never writes its own text for a refusal. [must, review]

## Code

- **TS-05** Use `strict` and `noUncheckedIndexedAccess`. Lint with the typescript-eslint `strictTypeChecked` and `stylisticTypeChecked` configurations. [must, eslint]
- **TS-06** Don't use `any`, the non-null `!`, or a type assertion. Only `as const` is allowed. [must, eslint]
- **TS-07** Type a value where it enters the program, with a valibot schema. Examples are a PostgREST answer, a response of the identity provider, a cookie, a request body, a form field and a message from a frame. [must, review]
- **TS-08** Derive the type with `v.InferOutput`. `lib/types.ts` has the schemas of the rows, so a column that a view loses fails at the first read, with the field in the message. [should, review]
- **TS-09** Use `type` for object types. Use `interface` only where a library needs declaration merging, such as `App.Locals` and `Window`. [must, eslint]
- **TS-10** Use named exports. A `.svelte` file and a SvelteKit route file are the only exceptions. [must, eslint]
- **TS-11** Return a `Result` for a failure that the caller can handle, such as an answer of PostgREST. Throw for an input that the program can't run without. [must, review]
- **TS-12** Write no comment inside a function. Write a one-line `/** */` summary above an exported function only when its name doesn't say enough. [must, code]
- **TS-13** Give a lint disable comment a reason. A marker that a tool reads, such as `// pragma: allowlist secret`, is allowed. [must, review]

```ts
✅ const row = v.parse(RowSchema, data);
❌ const row = data as Row;
```

## Tooling

`devenv --profile console tasks run console:lint:ui` runs `svelte-check`, ESLint (`eslint-plugin-svelte` recommended and typescript-eslint) and `prettier --check`, for `console/ui/` and `console/notebooks/`. Run `pnpm format` in the folder to format. prek runs the hooks `prettier` and `eslint` at commit time. They use the binaries of `console/ui/node_modules`, so run `pnpm install` once.

## Tests

- **TS-14** Test a unit with Vitest, in a `*.test.ts` file next to the module. Use a table of cases with a name for each case. [must, review]
- **TS-15** Write a browser test as a `playwright-bdd` scenario that a user could read. Let a step do one thing. [must, review]
- **TS-16** Keep scenarios independent, so they can run in parallel. Let a scenario create the data that it needs, with a name that no other scenario uses. [must, review]
- **TS-17** Put a scenario that changes data that others read in `tests/features/shared/`. [must, review]
- **TS-18** Tag a scenario that adds a schema or a table `@new-schema`, write each name with the step parameter `{name}`, and sign in as `web_root`. The parameter adds a random suffix. [must, review]
- **TS-19** Wait for "Saved." with `saved()`, or wait for the row that the action creates. A toast that an earlier step left on screen matches a later wait for the same text. [must, review]
- **TS-20** Wait for a condition, never for a time. Use a web-first assertion (`expect(...).toBeVisible()`), `expect.poll`, or the `hydrated` helper. [must, review]
- **TS-21** Wait a fixed time only to check that something doesn't happen. [must, review]
- **TS-22** Write no test for the notebooks (`console/notebooks/`): no unit test, no browser test, and no scenario that opens the notebook. [must, review]
- **TS-23** Hand state from one step to the next through the typed `world` fixture in `console/ui/tests/fixtures.ts`. Don't attach it to `page`. [must, review]

<!-- nav:start -->
---

[Previous: SQL](sql.md) | [Next: Svelte](svelte.md) | [Index](index.md) | [Home](../../index.md)
<!-- nav:end -->
