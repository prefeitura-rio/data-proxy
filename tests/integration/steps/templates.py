"""Integration steps for runtime Helm SQL template rendering."""

import asyncio
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from psycopg.sql import Identifier
from pytest_bdd import given, then, when

from data_proxy.settings import settings
from tests.fixtures.types import Postgres

TEMPLATE_ROOT = (
    Path(__file__).resolve().parents[3] / "helm" / "files" / "templates" / "postgres"
)
PROCEDURES = {
    "cleanup_table_state": "jsonb",
    "cleanup_stale_objects": "jsonb, text",
    "apply_retention": "jsonb, text",
    "prune_access_log": "interval, text",
}


@dataclass
class TemplateScenario:
    """State for one Helm template scenario."""

    postgres: Postgres
    rendered: dict[str, str]


def render_template(name: str, context: dict[str, str]) -> str:
    """Render one Helm SQL template with minijinja-cli."""
    with TemporaryDirectory() as directory:
        context_path = Path(directory) / "context.json"
        context_path.write_text(json.dumps(context))
        result = subprocess.run(  # noqa: S603
            [  # noqa: S607
                "minijinja-cli",
                "--strict",
                "--autoescape",
                "none",
                "--format",
                "json",
                str(TEMPLATE_ROOT / f"{name}.sql"),
                str(context_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    return result.stdout


@given(
    "a PostgreSQL database for Helm template rendering",
    target_fixture="template_context",
)
def helm_template_database(postgres: Postgres) -> TemplateScenario:
    """Provide a PostgreSQL database for rendered maintenance procedures."""
    schema = Identifier(settings.DBOS_APP_SCHEMA).as_string(None)
    return TemplateScenario(postgres=postgres, rendered={"schema": schema})


@when("I render and execute the Helm maintenance templates")
def execute_helm_maintenance_templates(template_context: TemplateScenario) -> None:
    """Render and execute every maintenance procedure template."""
    for procedure in PROCEDURES:
        sql = render_template(procedure, template_context.rendered)
        asyncio.run(template_context.postgres.connection.execute(sql.encode()))

    grant = render_template(
        "grant_migration_access",
        {**template_context.rendered, "user_role": '"user"'},
    )
    asyncio.run(template_context.postgres.connection.execute(grant.encode()))
    asyncio.run(template_context.postgres.connection.commit())


@then("the Helm maintenance procedures exist")
def helm_maintenance_procedures_exist(template_context: TemplateScenario) -> None:
    """Verify that every rendered procedure exists in PostgreSQL."""
    grant_sql = render_template(
        "grant_migration_access",
        {**template_context.rendered, "user_role": '"user"'},
    )
    assert "GRANT SELECT ON ALL TABLES" in grant_sql

    for procedure, signature in PROCEDURES.items():
        cursor = asyncio.run(
            template_context.postgres.connection.execute(
                b"SELECT to_regprocedure(%s)",
                (f"{settings.DBOS_APP_SCHEMA}.{procedure}({signature})",),
            )
        )
        assert asyncio.run(cursor.fetchone()) is not None
