"""Integration tests for the access-policy table that the sync service installs."""

import pytest
from psycopg.sql import SQL, Identifier

from data_proxy.models import SchemaConfig, SyncConfig
from tests.fixtures.types import Postgres
from tests.helpers import initialize_schemas, insert_access_policy, scalar

pytestmark = pytest.mark.postgres

SYNCHRONOUS_COMMIT = "SELECT current_setting('synchronous_commit')"


class TestAccessPolicyCommit:
    """A policy write waits until the standbys apply it, so no reader keeps a revoked grant."""

    async def test_policy_writes_wait_for_the_standbys_but_other_commits_do_not(
        self, postgres: Postgres
    ) -> None:
        schema = postgres.namespace.schema
        await initialize_schemas(
            postgres.backend, SyncConfig(schemas={schema: SchemaConfig()})
        )
        await postgres.connection.execute(
            SQL("SET ROLE {}").format(Identifier(f"policy_writer_{schema}"))
        )

        before = await scalar(postgres, SYNCHRONOUS_COMMIT)
        await insert_access_policy(postgres, "test_user_1", "unit", "unit_1")
        after = await scalar(postgres, SYNCHRONOUS_COMMIT)
        await postgres.connection.rollback()
        reset = await scalar(postgres, SYNCHRONOUS_COMMIT)

        assert (before, after, reset) == ("on", "remote_apply", "on")
