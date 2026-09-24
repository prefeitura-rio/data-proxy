"""Install DuckDB extensions at build time.

Run this during the Docker image build so runtime pods do not need
network access to download extensions. The script fails the build
if any extension cannot be installed.
"""

import duckdb


def main() -> None:
    """Install all required DuckDB extensions."""
    extensions = [
        "httpfs",
        "ducklake",
        "sqlite",
        "postgres_scanner",
    ]

    connection = duckdb.connect()

    for extension in extensions:
        connection.execute(f"INSTALL {extension}")
        connection.execute(f"LOAD {extension}")

    connection.execute("INSTALL bigquery FROM community")
    connection.execute("LOAD bigquery")

    print("DuckDB extensions installed successfully")


if __name__ == "__main__":
    main()
