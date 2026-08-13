"""BigQuery metadata helpers for table-level change detection."""

from google.cloud.bigquery import Client


def table_modified(client: Client, bq_table: str) -> str:
    """Return the table modification time in epoch milliseconds."""
    modified = client.get_table(bq_table).modified

    if modified is None:
        msg = f"Missing BigQuery modification time: {bq_table}"
        raise ValueError(msg)

    return str(int(modified.timestamp() * 1000))
