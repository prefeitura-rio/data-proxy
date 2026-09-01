"""BigQuery table reference and modification metadata helpers."""

from dataclasses import dataclass

from google.cloud.bigquery import Client


@dataclass(frozen=True, slots=True)
class TableReference:
    """Validated project, dataset, and table from a BigQuery reference."""

    project: str
    dataset: str
    table: str


def table_modified(client: Client, table: str) -> str:
    """Return the table modification time in epoch milliseconds."""
    modified = client.get_table(table).modified
    if modified is None:
        raise ValueError(f"Missing BigQuery modification time: {table}")
    return str(int(modified.timestamp() * 1000))


def parse_table_reference(table: str) -> TableReference:
    """Split a model-validated BigQuery table reference into its components."""
    project, dataset, table_name = table.split(".")
    return TableReference(project=project, dataset=dataset, table=table_name)
