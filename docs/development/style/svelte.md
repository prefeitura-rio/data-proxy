# Svelte

Applies to: `console/ui/src/**/*.svelte`.

## Runes and components

- **SV-01** Use runes only. Use `$state` for a value that changes what the page shows, and a plain variable for the rest. [must, eslint]
- **SV-02** Compute a value with `$derived`. Don't copy a prop or `data` into `$state` in an `$effect`: derive from the prop, and keep only the changed part in `$state`. [must, eslint]
- **SV-03** Use `$effect` only for a system outside Svelte, such as a timer or a listener, and return its cleanup. Use `{@attach}` to sync a DOM element, as `Modal.svelte` does. [must, review]
- **SV-04** Treat props as values that change. Derive a value that depends on a prop. [must, svelte-check]
- **SV-05** Use a keyed `{#each}` with a key that's unique to the item. Never use the index. [must, eslint]
- **SV-06** Write events as attributes (`onclick`). Don't use `on:`. [must, eslint]
- **SV-07** Pass every internal `href` through `resolve()` from `$app/paths`. For a query string, use a `GET` form. [must, eslint]
- **SV-08** Write each repeated piece of markup once, as a component in `lib/components/`. A component that belongs to one page stays in the folder of that route. [must, review]
- **SV-09** Keep a `.svelte` file to at most 80 lines of script and 150 lines of template. [must, review]

```svelte
✅ {#each rows as row (row.id)}
❌ {#each rows as row, i (i)}
```

## Forms

- **SV-10** Use `use:enhance` on every POST form, except sign-out. A GET form (a filter or a pager) doesn't use it. [must, review]
- **SV-11** Give every input a visible label (`Field` and `Check`). An input in a table row uses `aria-label`, because its column gives the visible name. [must, svelte-check]
- **SV-12** Give every POST form a `Submission` from `lib/enhance.svelte.ts` and a `SubmitButton`, so the form shows its pending state and sends one request. [must, review]
- **SV-13** Return `{ toast }` from a form action when it works. A dialog shows a refusal inside itself (`inline`), and the page shows the refusal of any other form. [must, review]
- **SV-14** Ask for a confirmation before a delete (`FormDialog` with `danger`), and hide the row until the answer comes (`begin` of the `Submission`). [must, review]
- **SV-15** Use `FilterForm` for the filters of a list and `Time` for a time. Don't write a timer in a page. [must, review]
- **SV-16** Reload live data with `poll(key)` and `depends(key)` from `lib/poll.svelte.ts`. [must, review]
- **SV-17** Style with Tailwind and DaisyUI classes. Import icons by name from `@lucide/svelte`. [must, review]

<!-- nav:start -->
---

[Previous: TypeScript](typescript.md) | [Next: Notebooks](notebooks.md) | [Index](index.md) | [Home](../../index.md)
<!-- nav:end -->
