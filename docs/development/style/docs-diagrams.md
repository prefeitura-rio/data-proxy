# Diagrams

Applies to: `docs/diagrams/**`, `docs/**/*.svg`.

## Where a diagram lives

- **DOC-24** Give a diagram one owner page. Other pages link to it and don't embed a copy. [must, review]
- **DOC-25** Give every diagram a short name, and write it as the alt text of the image. Don't add a caption line under the image. [must, review]
- **DOC-26** Draw one dependency diagram of the whole system, on `docs/architecture/index.md`. [must, review]
- **DOC-27** Draw sequence diagrams for what happens in order, and put the diagrams of a layer in `docs/diagrams/architecture/`. [must, review]
- **DOC-28** Draw only sequence diagrams on the pages of the serving, sync and console layers. A page about another topic may have a small dependency diagram. Use a table for anything else. [must, review]

## How to draw it

- **DOC-29** Write a diagram in D2 in `docs/diagrams/<name>.d2` or in a folder inside it, such as `docs/diagrams/schemas/`. Don't write a diagram in another language. [must, review]
- **DOC-30** Let `db.d2` import the other files of its folder. Edit a table in its own file and not in `db.d2`. [must, review]
- **DOC-31** Render a diagram with `devenv --profile docs tasks run docs:build:diag`. Commit the `.d2` and the `.svg`, and embed the SVG with an image link and an alt text. [must, devenv]
- **DOC-32** Know that `docs:lint:diag` (profile `docs`) fails when an SVG is out of date, and that CI doesn't run it. [should, devenv]
- **DOC-33** Make every diagram render in a light and a dark theme. [must, review]
- **DOC-34** Draw a database diagram with `sql_table` shapes, and take the columns from the SQL files. [must, review]

<!-- nav:start -->
---

[Previous: Documentation](docs.md) | [Next: Git](git.md) | [Index](index.md) | [Home](../../index.md)
<!-- nav:end -->
