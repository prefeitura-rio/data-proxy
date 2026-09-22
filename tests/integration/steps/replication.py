"""Integration steps for PostgreSQL replication readiness."""

import asyncio
from dataclasses import dataclass

from pytest_bdd import given, then, when

from data_proxy.replication import current_wal_lsn, replicas_replayed
from tests.fixtures.types import Postgres


@dataclass
class ReplicationScenario:
    postgres: Postgres


@given("a fresh PostgreSQL replication schema", target_fixture="replication_context")
def fresh_replication_schema(postgres: Postgres) -> ReplicationScenario:
    return ReplicationScenario(postgres=postgres)


@when("I read the current WAL position", target_fixture="wal_lsn")
def read_wal_lsn(replication_context: ReplicationScenario) -> str:
    return asyncio.run(current_wal_lsn(replication_context.postgres.connection))


@when("I check replica replay for a future LSN")
def check_replica_replay(replication_context: ReplicationScenario) -> None:
    asyncio.run(
        replicas_replayed(
            replication_context.postgres.connection,
            "FFFFFFFF/FFFFFFFF",
        )
    )


@then("a non-empty WAL LSN is returned")
def check_wal_lsn(wal_lsn: str) -> None:
    assert wal_lsn
    assert "/" in wal_lsn


@then("the replica replay check completes")
def check_replica_error() -> None:
    pass
