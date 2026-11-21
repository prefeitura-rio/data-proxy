# Projects

A project groups schemas under one name. The Config tab lists the schemas by project, and a project with no schema stays in the list. The project `default` always exists, and no token deletes it.

## Add a project and a schema

- Add a project with a name of lowercase letters and digits. A name that the system uses is refused, as [Names](../terraform.md#names) lists.
- Add a schema with a project and a short name. The database builds the physical name: `<project>__<short_name>`, also in `default`.
- A schema can't change its project or its short name. Delete it and add it again.
- Delete a project only when it has no schema.

## Who reads what

The `projects` claim of a token gives every schema of a project, also one that the project gets later. The [claims page](../security/claims.md) says how the claim works. The view `catalog` lists each project, schema and table with its `ltree` path. The row policies show a token only the rows that it may read.

<!-- nav:start -->
---

[Previous: Configuration](./configuration.md) | [Next: Changes](./changes.md) | [Index](./index.md) | [Home](../index.md)
<!-- nav:end -->
