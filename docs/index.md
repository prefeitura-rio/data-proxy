# Data Proxy

Data Proxy copies tables from a source, such as BigQuery, into PostgreSQL and serves them as a REST API. Each caller reads only the rows that its token and the access policy allow. This wiki explains how it works, how to use it and how to run it. The [Glossary](glossary.md) defines the terms.

```mermaid
{{#include diagrams/system-context.mmd}}
```

## Documentation downloads

Each docs release includes an [LLM bundle](./llms.txt) with all chapters as Markdown. This download belongs to the version of the site that you read. Only the complete bundle includes this download.

## Find your page

| I want to | Read |
| --- | --- |
| Query the data | [Using the API](using.md) |
| Understand the parts and how they fit | [Architecture](architecture/index.md) |
| Add or change a synced table | [Sync](./sync.md) and the [Console](console/index.md) |
| Decide who can read what | [Security](security/index.md) |
| Install, scale or back up the system | [Operations](operations/index.md) |
| Change the code | [Development](dev/index.md) and the [repository style guide](https://github.com/prefeitura-rio/data-proxy/blob/main/STYLEGUIDE.md) |
| Look up a word or a tool | [Glossary](glossary.md) |
