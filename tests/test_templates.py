"""Tests for SQL template substitution and identifier/literal safety."""

from psycopg import sql

from dp.templates import load_template


def test_plain_strings_pass_through_unescaped() -> None:
    """Raw strings substitute without quoting, for pre-validated keywords."""
    rendered = load_template(
        {"path": "duckdb/attach_postgres", "mapping": {"pg_dsn": "raw"}}
    )

    assert rendered == "ATTACH raw AS pg (TYPE postgres)\n"


def test_identifier_quotes_and_escapes() -> None:
    """Identifier values render as quoted, self-escaping SQL identifiers."""
    rendered = load_template(
        {
            "path": "pg/grant_select",
            "mapping": {
                "schema": sql.Identifier("public"),
                "table": sql.Identifier('has"quote'),
                "user_role": sql.Identifier("web_user"),
            },
        }
    )

    assert rendered == 'GRANT SELECT ON "public"."has""quote" TO "web_user"\n'


def test_identifier_neutralizes_injection_payload() -> None:
    """An injection payload used as an identifier renders as one inert token."""
    payload = 'x"; DROP TABLE s.t; --'

    rendered = load_template(
        {
            "path": "pg/grant_select",
            "mapping": {
                "schema": sql.Identifier("s"),
                "table": sql.Identifier(payload),
                "user_role": sql.Identifier("web_user"),
            },
        }
    )

    assert rendered == ('GRANT SELECT ON "s"."x""; DROP TABLE s.t; --" TO "web_user"\n')


def test_literal_quotes_and_escapes() -> None:
    """Literal values render as single-quoted, self-escaping SQL strings."""
    rendered = load_template(
        {
            "path": "duckdb/discover_partitions",
            "mapping": {
                "partition_column": sql.Identifier("dt"),
                "bq_table": sql.Literal("o'brien.dataset.table"),
            },
        }
    )

    assert rendered == (
        "SELECT DISTINCT \"dt\"\nFROM bigquery_scan('o''brien.dataset.table')\n"
    )


def test_literal_neutralizes_injection_payload() -> None:
    """An injection payload used as a literal renders as one inert string."""
    payload = "x') UNION SELECT * FROM read_csv('/etc/passwd"

    rendered = load_template(
        {
            "path": "duckdb/discover_partitions",
            "mapping": {
                "partition_column": sql.Identifier("dt"),
                "bq_table": sql.Literal(payload),
            },
        }
    )

    assert rendered == (
        'SELECT DISTINCT "dt"\n'
        "FROM bigquery_scan('x'') UNION SELECT * FROM read_csv(''/etc/passwd')\n"
    )


def test_identifier_list_joins_and_quotes_each_element() -> None:
    """Identifier lists render as a comma-separated, individually quoted list."""
    rendered = load_template(
        {
            "path": "pg/create_index",
            "mapping": {
                "name": sql.Identifier("idx_table"),
                "schema": sql.Identifier("s"),
                "table": sql.Identifier("t"),
                "columns": sql.SQL(", ").join(
                    sql.Identifier(column) for column in ["a", "b"]
                ),
            },
        }
    )

    assert (
        rendered
        == 'CREATE INDEX IF NOT EXISTS "idx_table"\n    ON "s"."t" ("a", "b")\n'
    )
