"""Tests for SQL template substitution and identifier/literal safety."""

from psycopg.sql import SQL, Identifier, Literal

from dp.models import (
    AllSelection,
    RangeSelection,
    RemainderSelection,
    TimeRangeSelection,
)
from dp.templates import load_template, read_template, selection_fields


def render(value: object) -> str:
    """Render one mapping value as psycopg would when substituting it."""
    assert isinstance(value, (SQL, Identifier, Literal))
    return value.as_string(None)


def test_runtime_freshness_schema_contract() -> None:
    """Runtime schema SQL contains the complete freshness contract."""
    roles = read_template("pg/init_roles")
    schema = read_template("pg/init_schema")

    assert "sync_status AS ENUM ('success', 'failure')" in roles
    for definition in (
        '"table" text NOT NULL',
        "strategy text NOT NULL",
        "partition text",
        "updated_at timestamptz",
        "attempted_at timestamptz NOT NULL",
        "status ${rls_schema}.sync_status NOT NULL",
        'UNIQUE NULLS NOT DISTINCT ("table", strategy, partition)',
        "GRANT SELECT ON ${schema}.freshness TO ${user_role}",
        "ALTER TABLE ${schema}.freshness ENABLE ROW LEVEL SECURITY",
        "CREATE POLICY schema_scope ON ${schema}.freshness",
    ):
        assert definition in schema


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
                "schema": Identifier("public"),
                "table": Identifier('has"quote'),
                "user_role": Identifier("user"),
            },
        }
    )

    assert rendered == 'GRANT SELECT ON "public"."has""quote" TO "user"\n'


def test_identifier_neutralizes_injection_payload() -> None:
    """An injection payload used as an identifier renders as one inert token."""
    payload = 'x"; DROP TABLE s.t; --'

    rendered = load_template(
        {
            "path": "pg/grant_select",
            "mapping": {
                "schema": Identifier("s"),
                "table": Identifier(payload),
                "user_role": Identifier("user"),
            },
        }
    )

    assert rendered == ('GRANT SELECT ON "s"."x""; DROP TABLE s.t; --" TO "user"\n')


def test_literal_quotes_and_escapes() -> None:
    """Literal values render as single-quoted, self-escaping SQL strings."""
    rendered = load_template(
        {
            "path": "duckdb/write_all",
            "mapping": {
                "columns": SQL("*"),
                "gcs_path": Literal("s3://b/t/data.parquet"),
                "bq_table": Literal("o'brien.dataset.table"),
            },
        }
    )

    assert rendered == (
        "COPY (SELECT * FROM bigquery_scan('o''brien.dataset.table')) TO "
        "'s3://b/t/data.parquet' (\n    FORMAT PARQUET\n)\n"
    )


def test_literal_neutralizes_injection_payload() -> None:
    """An injection payload used as a literal renders as one inert string."""
    payload = "x') UNION SELECT * FROM read_csv('/etc/passwd"

    rendered = load_template(
        {
            "path": "duckdb/write_all",
            "mapping": {
                "columns": SQL("*"),
                "gcs_path": Literal("s3://b/t/data.parquet"),
                "bq_table": Literal(payload),
            },
        }
    )

    assert rendered == (
        "COPY (SELECT * FROM bigquery_scan('x'') UNION SELECT * FROM "
        "read_csv(''/etc/passwd')) TO 's3://b/t/data.parquet' (\n"
        "    FORMAT PARQUET\n)\n"
    )


def test_selection_fields_returns_empty_mapping_for_all() -> None:
    """Selecting every row encodes no column or bound fields."""
    assert selection_fields(AllSelection()) == {}


def test_selection_fields_encodes_time_range_selection() -> None:
    """A time range selection encodes its column and date/timestamp bounds."""
    fields = selection_fields(
        TimeRangeSelection(column="dt", lower="2025-01-01", upper="2025-02-01")
    )

    assert render(fields["column"]) == '"dt"'
    assert render(fields["lower"]) == "'2025-01-01'"
    assert render(fields["upper"]) == "'2025-02-01'"


def test_selection_fields_encodes_range_selection() -> None:
    """A range selection encodes its column and inclusive/exclusive bounds."""
    fields = selection_fields(
        RangeSelection(partition_id="0", column="cpf", lower=0, upper=10)
    )

    assert render(fields["column"]) == '"cpf"'
    assert render(fields["lower"]) == "0"
    assert render(fields["upper"]) == "10"


def test_selection_fields_encodes_remainder_selection() -> None:
    """A remainder selection encodes its column and start/end as lower/upper."""
    fields = selection_fields(RemainderSelection(column="cpf", start=0, end=100))

    assert render(fields["column"]) == '"cpf"'
    assert render(fields["lower"]) == "0"
    assert render(fields["upper"]) == "100"


def test_identifier_list_joins_and_quotes_each_element() -> None:
    """Identifier lists render as a comma-separated, individually quoted list."""
    rendered = load_template(
        {
            "path": "pg/create_index",
            "mapping": {
                "name": Identifier("idx_table"),
                "schema": Identifier("s"),
                "table": Identifier("t"),
                "columns": SQL(", ").join(Identifier(column) for column in ["a", "b"]),
            },
        }
    )

    assert (
        rendered
        == 'CREATE INDEX IF NOT EXISTS "idx_table"\n    ON "s"."t" ("a", "b")\n'
    )
