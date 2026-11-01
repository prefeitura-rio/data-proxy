# Console lists, feedback and tokens

This page describes how the console shows lists, gives feedback and live data, and gets tokens from the identity provider. [Web app](app.md) describes the app itself.

## Lists

Every list pages. A page has 25 rows. The access policy and the tasks and steps of a sync run have 50 rows each, and the errors have 10 rows. The footer says which rows the page shows and how many rows there are.

PostgREST returns the total in the `Content-Range` header when the request sends `Prefer: count=exact`. The address contains the page and the filters, so you can bookmark a page.

The runs list filters by status, by schema, by a range of dates and by the first characters of a sync run id. The schema filter uses the column `schemas` of the view `runs`. The errors list, the access policy, the access log, the tables and the history filter by text or by a value. The log of a sync run shows 500 lines, and a button loads 500 more, up to 5000.

## Feedback and live data

Each action shows what it does. The submit button of a form shows a spinner and stays disabled until the server answers, so a double click sends one request. A saved change shows a toast at the bottom right that leaves after four seconds. A refusal stays in the dialog that sent it, so the user can fix the value without a new click.

A delete asks for a confirmation in a dialog. The row leaves the list immediately, and it comes back with the message of the database if the database refuses the delete.

A filter above a list applies after the user stops typing for 300 ms. The page keeps the focus and the scroll position, and the address keeps the filter. A **Clear** button removes the filters of that list.

The Sync tab and the page of a sync run reload their data at the interval that the user picks. The choices are off, 2, 5, 10, 30 and 60 seconds. The default is 5 seconds. The choice stays in the browser. The reload pauses while the tab is hidden and runs again when the tab shows.

The console reloads by polling because it has no push channel. It reads through PostgREST, and DBOS sends no notification when a workflow changes its status. A push needs a direct database connection and triggers on tables that DBOS owns. The log of a run is the exception. It streams over server-sent events.

The log of a run has the lines of every workflow of the run: the plan, each dump task, each publish and the finish. Each line shows its schema, its table and its fields. The user can filter the log by table.

The log of a run follows its end while the user stays at the bottom. A scroll up stops the follow, and a **Jump to latest** button starts it again. The user can filter the log by level and by text, wrap the lines, copy the text and download it.

## Tokens from the identity provider

The console sends the access token of the user to PostgREST. PostgREST checks it against the keys of the identity provider, like it does for the data API. The token needs these claims besides the standard ones:

| Claim | Contains |
| --- | --- |
| `role` | One role of `rbac.toml`, such as `dp_console`. It's the database role that PostgREST switches to. |
| `schemas` | The list of schemas that the user manages, such as `["sales", "hr"]`. |

The `preferred_username` claim names the user in `updated_by` and `started_by`.

See [Theme](theme.md) for the colors and the theme button.

<!-- nav:start -->
---

[Previous: Application](app.md) | [Next: Theme](theme.md) | [Index](app.md) | [Home](../index.md)
<!-- nav:end -->
