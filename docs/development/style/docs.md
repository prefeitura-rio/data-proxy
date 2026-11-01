# Documentation

Applies to: `docs/**`, `README.md`, `AGENTS.md`, `STYLEGUIDE.md`.

## Writing

- **DOC-01** Use Simplified Technical English: short sentences, active voice, one idea per sentence. [must, vale]
- **DOC-02** Keep a sentence at 25 words or fewer and a paragraph at 6 sentences or fewer. Split a longer text into a list or a table. [must, review]
- **DOC-03** Expand an acronym at its first use on a page, and define each project term once in `docs/glossary.md`. Add a row to its Tools table for each new tool. [must, vale]
- **DOC-04** Use the fixed terms `access policy` (the table), `row policy` (the PostgreSQL rule) and `sync run`. [must, review]
- **DOC-05** Write `contains`, `stores` or `has`, and not `holds`. Write `immediately`, and not `at once`. Vale enforces the word swaps and the ban on semicolons. [must, vale]
- **DOC-06** Use contractions in Markdown. The Python and Nushell checks turn that rule off, because messages keep their full forms. [must, vale]
- **DOC-07** Rephrase a sentence that Vale flags, such as the acronym for a web address, some adverbs and acronyms in headings. Don't ignore the message. [must, vale]
- **DOC-08** Use generic examples unless a real value is required. [should, review]
- **DOC-09** Say "the source" for the product, and name BigQuery only on a page about the BigQuery adapter or for a value that's specific to it. [must, review]
- **DOC-10** Verify claims against the source, the Helm templates, the tests, or a command that you ran. [must, review]

## Pages and links

- **DOC-11** Write the documents in `docs/`. The home page is `docs/index.md`, and each folder has an `index.md` that lists its pages. [must, review]
- **DOC-12** Name a file with one word, and use two words only if one word is unclear. [should, review]
- **DOC-13** Give each topic one page, and link to it from the other pages. Don't copy text. Keep a file near 800 words, and split a larger file by topic. [must, review]
- **DOC-14** Link to another page with a relative Markdown link: the path to the `.md` file and the slug of the heading (lower case, spaces as hyphens), as in `[Roles](../security/roles.md#roles)`. Link to a folder with its `index.md`. [must, devenv]
- **DOC-15** Run `docs:lint:links` after you move a page or a heading. Marksman completes and follows the links in the editor, but it doesn't flag a broken one. [must, devenv]
- **DOC-16** End every page with the navigation block (Previous, Next, Index and Home) that `docs:build:nav` writes. Don't edit the block by hand. [must, devenv]
- **DOC-17** Put the page order in `docs/.nav.json`. Add a new page to that file, and link it from its parent page. [must, devenv]
- **DOC-18** Expect `docs:lint:nav` to fail on a missing page, a page without a link from its parent, or an old navigation block. [must, devenv]
- **DOC-19** Don't write by hand a table that a file already defines. `docs/security/roles.md` has a role table that `docs:build:roles` writes from `rbac.toml`, and `docs:lint:roles` checks it. [must, devenv]
- **DOC-20** Run `nix:lint:cmd` after you rename a task or a profile. It fails on a `devenv` command in a page that names a profile or a task that doesn't exist. [must, devenv]

## Vale

- **DOC-21** Let the `vale` git hook check the staged `*.md`, `*.py` and `*.nu` files at commit time. Run `vale <file>` in the `repo` shell to check a file earlier. [must, vale]
- **DOC-22** Let `DataProxy.Terms` fix the casing of product names: PostgreSQL, PostgREST, SeaweedFS, DuckDB and BigQuery. [must, vale]
- **DOC-23** Add a word to `.vale/styles/config/vocabularies/data-proxy/accept.txt` at the end of the file only. [must, vale]
- **DOC-35** Write a style page as a list of rules in the format of the [style index](index.md). Keep a rule to 40 words and a page to 900 words. [must, devenv]

<!-- nav:start -->
---

[Previous: Notebooks](notebooks.md) | [Next: Diagrams](docs-diagrams.md) | [Index](index.md) | [Home](../../index.md)
<!-- nav:end -->
