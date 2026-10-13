"""Mutate the dedicated BigQuery snapshot fixture from a copied sync Pod."""

from os import environ
from sys import argv

from google.cloud.bigquery import Client, QueryJobConfig, ScalarQueryParameter

SNAPSHOT_ID = "snapshot-row"


def version_argument() -> str:
    """Return the one required snapshot version command-line option."""
    match argv[1:]:
        case ["--version", version] if version != "":
            return version
        case _:
            raise ValueError("usage: mutate.py --version <version>")


def main() -> None:
    """Update the fixture version and fail unless exactly one row changed."""
    version = version_argument()
    source = environ["SNAPSHOT_SOURCE"]

    client = Client(project=source.split(".", 1)[0])
    job = client.query(
        f"UPDATE `{source}` SET version = @version WHERE id = @id",
        job_config=QueryJobConfig(
            query_parameters=[
                ScalarQueryParameter("version", "STRING", version),
                ScalarQueryParameter("id", "STRING", SNAPSHOT_ID),
            ]
        ),
    )

    result = job.result()

    if result.num_dml_affected_rows != 1:
        raise RuntimeError(
            f"snapshot fixture mutation affected {result.num_dml_affected_rows} rows"
        )


if __name__ == "__main__":
    main()
