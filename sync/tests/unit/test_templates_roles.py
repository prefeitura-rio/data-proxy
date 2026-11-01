"""Tests for the roles and the rbac templates."""

import operator
import re
from collections.abc import Callable
from random import Random
from typing import Final

import psycopg
import pytest
from hypothesis import given
from hypothesis import strategies as st
from psycopg.rows import TupleRow
from psycopg.sql import SQL, Identifier, Literal

from tests.constants import (
    REQUEST_ROLES,
    SIGNER,
)
from tests.fixtures.types import Postgres
from tests.helpers import (
    RBAC,
    SOURCE_HASH,
    RbacRole,
    RbacRules,
    answers_for,
    create_request_roles,
    create_schema_reader,
    helm_sql,
    install_rbac,
    install_rbac_sync,
    make_serving_login,
    query_plan,
    revision,
    scalar,
    set_setting,
    stored_roles,
    stored_roles_sync,
    verify_rbac,
)
from tests.strategies import (
    PROPERTIES,
    ROLE_POOL,
    claim_and_schema,
    rbac_rules,
)


@pytest.mark.postgres
class TestRolesTemplate:
    """The create_roles template: the roles that the requests use, and how they relate."""

    async def test_creates_the_request_roles_without_a_login(
        self, postgres: Postgres
    ) -> None:
        """The anonymous role and each role of the rules file exist and can't log in."""
        await create_request_roles(postgres, "first")
        cursor = await postgres.connection.execute(
            b"SELECT rolname, rolcanlogin FROM pg_roles WHERE rolname LIKE ANY (ARRAY['dp\\_t\\_%', 'anon\\_t']) ORDER BY 1"
        )
        assert await cursor.fetchall() == [
            ("anon_t", False),
            ("dp_t_admin", False),
            ("dp_t_api", False),
            ("dp_t_policy", False),
        ]

    async def test_every_role_but_the_baseline_is_a_member_of_the_baseline(
        self, postgres: Postgres
    ) -> None:
        """The baseline role has every other role of the rules file as a member, and the serving login."""
        await create_request_roles(postgres, "first")
        cursor = await postgres.connection.execute(
            b"""
            SELECT member.rolname FROM pg_auth_members AS m
            INNER JOIN pg_roles AS member ON member.oid = m.member
            INNER JOIN pg_roles AS parent ON parent.oid = m.roleid
            WHERE parent.rolname = 'dp_t_api' ORDER BY 1
            """
        )
        assert [row[0] for row in await cursor.fetchall()] == [
            "auth_t",
            "dp_t_admin",
            "dp_t_policy",
        ]

    async def test_the_serving_login_logs_in_and_does_not_inherit_the_request_roles(
        self, postgres: Postgres
    ) -> None:
        """The serving login has a password login, no inheritance, and every request role as a membership."""
        await create_request_roles(postgres, "first")
        cursor = await postgres.connection.execute(
            b"SELECT rolcanlogin, rolinherit, rolpassword LIKE 'SCRAM-SHA-256$%' FROM pg_authid WHERE rolname = 'auth_t'"
        )
        assert await cursor.fetchone() == (True, False, True)
        cursor = await postgres.connection.execute(
            b"""
            SELECT parent.rolname FROM pg_auth_members AS m
            INNER JOIN pg_roles AS parent ON parent.oid = m.roleid
            INNER JOIN pg_roles AS member ON member.oid = m.member
            WHERE member.rolname = 'auth_t' ORDER BY 1
            """
        )
        assert [row[0] for row in await cursor.fetchall()] == [
            "anon_t",
            "dp_t_admin",
            "dp_t_api",
            "dp_t_policy",
        ]

    async def test_the_signer_logs_in_and_belongs_to_no_request_role(
        self, postgres: Postgres
    ) -> None:
        """The signer has a password login and no inheritance, and no membership until a schema reader grants one."""
        await create_request_roles(postgres, "first")
        cursor = await postgres.connection.execute(
            SQL(
                "SELECT rolcanlogin, rolinherit, rolpassword LIKE 'SCRAM-SHA-256$%' FROM pg_authid WHERE rolname = {}"
            ).format(Literal(SIGNER))
        )
        assert await cursor.fetchone() == (True, False, True)
        cursor = await postgres.connection.execute(
            SQL(
                "SELECT count(*) FROM pg_auth_members AS m INNER JOIN pg_roles AS member ON member.oid = m.member WHERE member.rolname = {}"
            ).format(Literal(SIGNER))
        )
        assert await cursor.fetchone() == (0,)

    async def test_a_serving_login_that_exists_without_a_login_gets_one(
        self, postgres: Postgres
    ) -> None:
        """pg_duckdb creates the role of duckdb.postgres_role without a login, so the template sets the login."""
        await postgres.connection.execute(b"CREATE ROLE auth_t")
        await create_request_roles(postgres, "first")
        cursor = await postgres.connection.execute(
            b"SELECT rolcanlogin, rolinherit FROM pg_roles WHERE rolname = 'auth_t'"
        )
        assert await cursor.fetchone() == (True, False)

    async def test_every_request_role_gets_the_statement_timeout_of_the_data_api(
        self, postgres: Postgres
    ) -> None:
        """Each request role carries its own statement timeout, because a membership doesn't pass settings on."""
        await create_request_roles(postgres, "first")
        cursor = await postgres.connection.execute(
            b"""
            SELECT r.rolname, s FROM pg_db_role_setting AS d
            INNER JOIN pg_roles AS r ON r.oid = d.setrole
            CROSS JOIN LATERAL unnest(d.setconfig) AS s
            WHERE r.rolname LIKE ANY (ARRAY['dp\\_t\\_%', 'anon\\_t']) ORDER BY 1
            """
        )
        assert await cursor.fetchall() == [
            ("anon_t", "statement_timeout=15s"),
            ("dp_t_admin", "statement_timeout=15s"),
            ("dp_t_api", "statement_timeout=15s"),
            ("dp_t_policy", "statement_timeout=15s"),
        ]

    async def test_a_second_run_succeeds_and_sets_the_password_again(
        self, postgres: Postgres
    ) -> None:
        """A second run changes nothing but the password, and creates no role twice."""
        await create_request_roles(postgres, "first")
        first = await scalar(
            postgres, "SELECT rolpassword FROM pg_authid WHERE rolname = 'auth_t'"
        )
        await create_request_roles(postgres, "second")
        second = await scalar(
            postgres, "SELECT rolpassword FROM pg_authid WHERE rolname = 'auth_t'"
        )
        assert first != second
        cursor = await postgres.connection.execute(
            b"SELECT count(*) FROM pg_roles WHERE rolname LIKE ANY (ARRAY['dp\\_t\\_%', 'anon\\_t', 'auth\\_t'])"
        )
        assert await cursor.fetchone() == (len(REQUEST_ROLES) + 2,)


@pytest.mark.postgres
class TestSchemaReaderTemplates:
    """The reader template gives a role the metadata of one schema and nothing else."""

    async def prepare(self, pg: Postgres) -> None:
        """Create the signer login and the metadata schemas of two schemas, each with one table."""
        await pg.connection.execute(
            SQL("CREATE ROLE {} LOGIN NOINHERIT").format(Identifier(SIGNER))
        )
        for name in ("sales", "hr"):
            await pg.connection.execute(
                SQL(
                    "CREATE SCHEMA {schema}; CREATE TABLE {schema}.files (path text); INSERT INTO {schema}.files VALUES ({path})"
                ).format(schema=Identifier(name), path=Literal(f"{name}/a.parquet"))
            )
            await create_schema_reader(pg, name)

    async def read_as(
        self, pg: Postgres, role: str, query: str
    ) -> list[tuple[str]] | str:
        """Run a query as a role, and return the rows or the message of the refusal."""
        await pg.connection.execute(SQL("SET LOCAL ROLE {}").format(Identifier(SIGNER)))
        await pg.connection.execute(SQL("SET LOCAL ROLE {}").format(Identifier(role)))
        try:
            cursor = await pg.connection.execute(query.encode())
            return [(str(row[0]),) for row in await cursor.fetchall()]
        except psycopg.errors.InsufficientPrivilege as error:
            return str(error.diag.message_primary)

    async def test_the_role_reads_its_own_metadata(self, postgres: Postgres) -> None:
        """The signer switches into the role of a schema and reads that schema."""
        await self.prepare(postgres)
        assert await self.read_as(
            postgres, "schema_sales", "SELECT path FROM sales.files"
        ) == [("sales/a.parquet",)]

    async def test_the_role_cannot_read_the_metadata_of_another_schema(
        self, postgres: Postgres
    ) -> None:
        """The role of one schema gets a refusal on the schema of another."""
        await self.prepare(postgres)
        refusal = await self.read_as(
            postgres, "schema_sales", "SELECT path FROM hr.files"
        )

        assert refusal == "permission denied for schema hr"

    async def test_the_role_cannot_write_the_metadata_of_its_own_schema(
        self, postgres: Postgres
    ) -> None:
        """The grant is a read grant."""
        await self.prepare(postgres)
        refusal = await self.read_as(
            postgres,
            "schema_sales",
            "INSERT INTO sales.files VALUES ('x') RETURNING path",
        )

        assert refusal == "permission denied for table files"

    async def test_the_role_reads_a_table_that_the_catalog_creates_later(
        self, postgres: Postgres
    ) -> None:
        """DuckLake creates its tables after the role. A default privilege covers them."""
        await self.prepare(postgres)
        await postgres.connection.execute(SQL("CREATE TABLE sales.later (path text)"))
        assert await self.read_as(
            postgres, "schema_sales", "SELECT count(*)::text FROM sales.later"
        ) == [("0",)]

    async def test_a_second_run_keeps_the_grants_of_the_role(
        self, postgres: Postgres
    ) -> None:
        """Run the template again, and the role still reads its schema and nothing else."""
        await self.prepare(postgres)

        await create_schema_reader(postgres, "sales")

        assert await self.read_as(
            postgres, "schema_sales", "SELECT path FROM sales.files"
        ) == [("sales/a.parquet",)]

    async def test_drop_removes_the_role_and_keeps_the_schema(
        self, postgres: Postgres
    ) -> None:
        """The drop template removes the role and its grants. Another run does nothing."""
        await self.prepare(postgres)

        await create_schema_reader(postgres, "sales", "drop_schema_reader")
        await create_schema_reader(postgres, "sales", "drop_schema_reader")

        cursor = await postgres.connection.execute(
            b"SELECT rolname FROM pg_roles WHERE rolname LIKE 'schema\\_%'"
        )
        assert await cursor.fetchall() == [("schema_hr",)]

    async def test_signer_keys_are_the_only_table_that_the_signer_writes(
        self, postgres: Postgres
    ) -> None:
        """The signer stores and reads the sealed keys of its identities, and it can't delete them or read anything else."""
        await self.prepare(postgres)
        await postgres.connection.execute(
            helm_sql("create_signer_keys", {"signer": Literal(SIGNER)}).encode()
        )
        await postgres.connection.execute(
            SQL("SET LOCAL ROLE {}").format(Identifier(SIGNER))
        )

        await postgres.connection.execute(
            b"INSERT INTO signer.keys (schema_name, access_key, secret_sealed) VALUES ('sales', 'AKIA', 'sealed')"
        )
        cursor = await postgres.connection.execute(
            b"SELECT access_key, secret_sealed FROM signer.keys"
        )
        assert await cursor.fetchall() == [("AKIA", "sealed")]

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await postgres.connection.execute(b"DELETE FROM signer.keys")

    async def test_signer_keys_run_again_without_an_error(
        self, postgres: Postgres
    ) -> None:
        """The template creates nothing twice, and it keeps the rows."""
        await self.prepare(postgres)
        template = helm_sql("create_signer_keys", {"signer": Literal(SIGNER)}).encode()
        await postgres.connection.execute(template)
        await postgres.connection.execute(
            b"INSERT INTO signer.keys (schema_name, access_key, secret_sealed) VALUES ('sales', 'AKIA', 'sealed')"
        )

        await postgres.connection.execute(template)

        assert await scalar(postgres, "SELECT count(*) FROM signer.keys") == 1

    async def test_drop_forgets_the_keys_of_the_schema(
        self, postgres: Postgres
    ) -> None:
        """A removed schema leaves no sealed key behind, and the other schema keeps its own."""
        await self.prepare(postgres)
        await postgres.connection.execute(
            helm_sql("create_signer_keys", {"signer": Literal(SIGNER)}).encode()
        )
        await postgres.connection.execute(
            b"INSERT INTO signer.keys (schema_name, access_key, secret_sealed) VALUES ('sales', 'A', 's'), ('hr', 'B', 's')"
        )

        await create_schema_reader(postgres, "sales", "drop_schema_reader")

        cursor = await postgres.connection.execute(
            b"SELECT schema_name FROM signer.keys"
        )
        assert await cursor.fetchall() == [("hr",)]

    async def test_a_name_that_makes_the_role_too_long_fails(
        self, postgres: Postgres
    ) -> None:
        """A role name over 63 characters would collide with another after the truncation."""
        await postgres.connection.execute(
            SQL("CREATE ROLE {} LOGIN NOINHERIT").format(Identifier(SIGNER))
        )
        name = "a" * 60

        await postgres.connection.execute(
            SQL("CREATE SCHEMA {}").format(Identifier(name))
        )

        with pytest.raises(psycopg.errors.RaiseException, match="too long"):
            await create_schema_reader(postgres, name)


@pytest.mark.postgres
class TestRbacTemplate:
    """The create_rbac template: the rules in the file against the answers of rbac.can and rbac.mine."""

    @pytest.mark.parametrize(
        "role",
        [
            *(pytest.param(role.name, id=role.name) for role in RBAC.roles),
            pytest.param("dp_ghost", id="unknown-role"),
            pytest.param("", id="empty-role"),
        ],
    )
    async def test_can_answers_what_the_rules_give_to_the_role(
        self, rbac: Postgres, role: str
    ) -> None:
        """Every permission is allowed exactly when the role or a role that it inherits holds it."""
        await set_setting(rbac, "app.claim_role", role)
        await rbac.connection.execute(b"SELECT rbac.load()")

        answers = {
            feature: await scalar(rbac, "SELECT rbac.can(%s)", feature)
            for feature in RBAC.features
        }

        expected = RBAC.effective(role)
        assert answers == {feature: feature in expected for feature in RBAC.features}

    async def test_can_denies_every_permission_without_a_role_claim(
        self, rbac: Postgres
    ) -> None:
        """A request without a token has no role claim and no permission."""
        await rbac.connection.execute(b"SELECT rbac.load()")

        assert [
            await scalar(rbac, "SELECT rbac.can(%s)", feature)
            for feature in RBAC.features
        ] == [False] * len(RBAC.features)

    @pytest.mark.parametrize(
        "feature", ["nope_nope", "", "read_api,read_sync", "read_api,", ",read_api"]
    )
    async def test_can_raises_for_a_permission_that_the_file_does_not_list(
        self, rbac: Postgres, feature: str
    ) -> None:
        """A name that no rule defines is a mistake of the code, so it fails loudly."""
        await set_setting(rbac, "app.claim_role", "dp_admin")
        await rbac.connection.execute(b"SELECT rbac.load()")

        with pytest.raises(psycopg.Error) as error:
            async with rbac.connection.transaction():
                await scalar(rbac, "SELECT rbac.can(%s)", feature)

        assert error.value.sqlstate == "PT500"

    @pytest.mark.parametrize(
        ("claim", "schema", "expected"),
        [
            pytest.param("s1,s2", "s1", True, id="first-of-two"),
            pytest.param("s1,s2", "s2", True, id="second-of-two"),
            pytest.param("s1", "s1", True, id="only-one"),
            pytest.param("s1,s2", "s", False, id="prefix-of-a-name"),
            pytest.param("s1,s2", "s10", False, id="longer-than-a-name"),
            pytest.param("s1,s2", "other", False, id="outside-the-claim"),
            pytest.param("s1,s2", "", False, id="empty-schema"),
            pytest.param("", "s1", False, id="empty-claim"),
            pytest.param("s1,s2", "s1,s2", False, id="comma-spans-two-names"),
            pytest.param("s1,s2", "s1,", False, id="trailing-comma"),
            pytest.param("s1,s2", ",s2", False, id="leading-comma"),
            pytest.param("s1,s2", ",", False, id="only-a-comma"),
            pytest.param("*", "s1", True, id="wildcard-reaches-any-schema"),
            pytest.param("s1,*", "other", True, id="wildcard-after-a-name"),
            pytest.param("*", "", False, id="wildcard-still-needs-a-name"),
            pytest.param("*", "s1,s2", False, id="wildcard-does-not-span-two-names"),
            pytest.param("s1", "*", False, id="name-is-not-the-wildcard"),
            pytest.param("s*", "s1", False, id="star-inside-a-name-is-no-wildcard"),
            pytest.param("s1,*", "*", True, id="wildcard-asks-for-itself"),
            pytest.param("s1:*", "s1", True, id="schema-wildcard-reaches-its-schema"),
            pytest.param("s1:*", "s2", False, id="schema-wildcard-stays-in-its-schema"),
            pytest.param("s1:*", "*", False, id="schema-wildcard-is-not-the-wildcard"),
            pytest.param(
                "s1,s2:*", "s1", True, id="plain-name-beside-a-schema-wildcard"
            ),
            pytest.param(
                "s1:*", "s1:", False, id="schema-wildcard-needs-the-whole-name"
            ),
        ],
    )
    async def test_mine_answers_whether_the_schema_is_in_the_claim(
        self, rbac: Postgres, claim: str, schema: str, expected: bool
    ) -> None:
        """A schema is mine only when the schemas claim names it exactly."""
        await set_setting(rbac, "app.claim_schemas", claim)
        await rbac.connection.execute(b"SELECT rbac.load()")

        assert await scalar(rbac, "SELECT rbac.mine(%s)", schema) is expected

    async def test_mine_denies_every_schema_without_a_schemas_claim(
        self, rbac: Postgres
    ) -> None:
        """A request without the claim reaches no schema."""
        await rbac.connection.execute(b"SELECT rbac.load()")

        assert await scalar(rbac, "SELECT rbac.mine(%s)", "s1") is False

    @pytest.mark.parametrize(
        ("claim", "schema", "expected"),
        [
            pytest.param("s1", "s1", False, id="plain-name-keeps-the-row-filter"),
            pytest.param("s1:*", "s1", True, id="schema-wildcard-reads-every-row"),
            pytest.param("s1:*", "s2", False, id="schema-wildcard-stays-in-its-schema"),
            pytest.param("s1,s2:*", "s1", False, id="only-the-marked-schema"),
            pytest.param(
                "s1,s2:*", "s2", True, id="the-marked-schema-beside-a-plain-one"
            ),
            pytest.param("*", "s1", True, id="wildcard-reads-every-row-of-any-schema"),
            pytest.param("*", "", False, id="wildcard-still-needs-a-name"),
            pytest.param("s1:*", "s1,s2", False, id="comma-spans-two-names"),
            pytest.param(
                "s1:*", "s1:", False, id="schema-wildcard-needs-the-whole-name"
            ),
            pytest.param("", "s1", False, id="empty-claim"),
        ],
    )
    async def test_all_rows_answers_whether_the_claim_lifts_the_row_filter_of_the_schema(
        self, rbac: Postgres, claim: str, schema: str, expected: bool
    ) -> None:
        """Only the element schema:* or the element * lifts the row filter, and schema:* only for its schema."""
        await set_setting(rbac, "app.claim_schemas", claim)
        await rbac.connection.execute(b"SELECT rbac.load()")

        assert await scalar(rbac, "SELECT rbac.all_rows(%s)", schema) is expected

    async def test_all_rows_denies_every_schema_without_a_schemas_claim(
        self, rbac: Postgres
    ) -> None:
        """A request without the claim reads no row."""
        await rbac.connection.execute(b"SELECT rbac.load()")

        assert await scalar(rbac, "SELECT rbac.all_rows(%s)", "s1") is False

    @pytest.mark.parametrize(
        "rules",
        [
            pytest.param(
                RBAC.changed("dp_console", inherits=["dp_ghost"]), id="unknown-parent"
            ),
            pytest.param(
                RBAC.changed("dp_api", permissions=["read_ghost"]),
                id="unknown-permission",
            ),
            pytest.param(
                RBAC.changed("dp_api", inherits=["dp_admin"]), id="inheritance-cycle"
            ),
            pytest.param(
                RBAC.changed("dp_api", inherits=["dp_api"]), id="role-inherits-itself"
            ),
            pytest.param(
                RBAC.model_copy(update={"roles": [*RBAC.roles, RBAC.roles[0]]}),
                id="repeated-role",
            ),
        ],
    )
    async def test_load_refuses_a_file_that_is_not_consistent_and_keeps_the_old_rules(
        self, rbac: Postgres, rules: RbacRules
    ) -> None:
        """One error rolls the whole load back, so the rules in the table stay as they were."""
        before = await stored_roles(rbac)

        with pytest.raises(psycopg.Error):
            async with rbac.connection.transaction():
                await install_rbac(rbac, rules)

        assert await stored_roles(rbac) == before

    async def test_load_refuses_a_role_that_does_not_exist_in_the_database(
        self, rbac: Postgres
    ) -> None:
        """The chart creates the roles of the file, so a role without a database role is a mismatch."""
        ghost = RbacRole(
            name="dp_missing", description="No database role", permissions=["read_api"]
        )

        with pytest.raises(psycopg.errors.UndefinedObject):
            async with rbac.connection.transaction():
                await install_rbac(
                    rbac, RBAC.model_copy(update={"roles": [*RBAC.roles, ghost]})
                )

    async def test_load_stores_the_closure_of_every_role_and_is_repeatable(
        self, rbac: Postgres
    ) -> None:
        """The table holds the inherited permissions, sorted, and a second load changes nothing."""
        first = await stored_roles(rbac)
        await install_rbac(rbac)

        assert await stored_roles(rbac) == first
        assert first == sorted(
            (role.name, sorted(RBAC.effective(role.name))) for role in RBAC.roles
        )

    async def test_load_removes_a_role_that_the_file_no_longer_has(
        self, rbac: Postgres
    ) -> None:
        """A role that left the file leaves the table at the next load."""
        extra = RbacRole(
            name="dp_extra", description="Test role", permissions=["read_api"]
        )
        await rbac.connection.execute(b"CREATE ROLE dp_extra NOLOGIN")
        await install_rbac(
            rbac, RBAC.model_copy(update={"roles": [*RBAC.roles, extra]})
        )
        assert "dp_extra" in [name for name, _ in await stored_roles(rbac)]

        await install_rbac(rbac)

        assert "dp_extra" not in [name for name, _ in await stored_roles(rbac)]

    async def test_load_records_the_hash_of_the_file_on_the_table(
        self, rbac: Postgres
    ) -> None:
        """The comment of the table says which version of the file it holds."""
        assert (
            await scalar(
                rbac, "SELECT obj_description('rbac.roles'::regclass, 'pg_class')"
            )
            == f"sha256:{SOURCE_HASH}"
        )

    async def test_load_raises_the_revision(self, rbac: Postgres) -> None:
        """Each load of the rules moves the revision, also when the file didn't change."""
        before = await revision(rbac)
        await install_rbac(rbac)

        assert await revision(rbac) > before

    async def test_load_makes_can_follow_the_new_rules(self, rbac: Postgres) -> None:
        """A second load with other rules changes the answer of rbac.can at once."""
        await set_setting(rbac, "app.claim_role", "dp_api")
        await rbac.connection.execute(b"SELECT rbac.load()")
        assert await scalar(rbac, "SELECT rbac.can('read_api')") is True

        await install_rbac(rbac, RBAC.changed("dp_api", permissions=[]))
        await rbac.connection.execute(b"SELECT rbac.load()")

        assert await scalar(rbac, "SELECT rbac.can('read_api')") is False

    async def test_can_and_mine_are_inlined_into_the_query(
        self, rbac: Postgres
    ) -> None:
        """The planner replaces both calls with their expression, so a check costs no function call."""
        can = await query_plan(
            rbac, "SELECT 1 FROM generate_series(1, 3) AS g WHERE rbac.can('read_sync')"
        )
        mine = await query_plan(
            rbac,
            "SELECT 1 FROM generate_series(1, 3) AS g WHERE rbac.mine('s' || g)",
        )

        assert ("rbac.can(" in can, "strpos(" in can) == (False, True)
        assert ("rbac.mine(" in mine, "strpos(" in mine) == (False, True)


@pytest.mark.postgres
class TestVerifyRbacTemplate:
    """The verify_rbac template: the roles of the database against the roles of the file."""

    async def test_passes_when_the_database_matches_the_file(
        self, rbac: Postgres
    ) -> None:
        """The roles exist, the serving login is a member of each, and nothing else is."""
        await make_serving_login(rbac)

        await verify_rbac(rbac)

    @pytest.mark.parametrize(
        ("setup", "extra_roles", "message"),
        [
            pytest.param(
                None, ("dp_missing",), "dp_missing", id="role-without-a-database-role"
            ),
            pytest.param(
                "REVOKE dp_admin FROM auth_v",
                (),
                "auth_v is not a member of dp_admin",
                id="serving-login-misses-a-role",
            ),
            pytest.param(
                "CREATE ROLE dp_stale NOLOGIN; GRANT dp_stale TO auth_v",
                (),
                "dp_stale",
                id="serving-login-has-a-role-that-left-the-file",
            ),
            pytest.param(
                "ALTER ROLE dp_api LOGIN", (), "dp_api can log in", id="role-can-log-in"
            ),
            pytest.param(
                "ALTER ROLE dp_api SUPERUSER",
                (),
                "dp_api is a superuser",
                id="role-is-a-superuser",
            ),
            pytest.param(
                "ALTER ROLE auth_v INHERIT",
                (),
                "auth_v inherits",
                id="serving-login-inherits",
            ),
            pytest.param(
                "DELETE FROM rbac.roles WHERE name = 'dp_admin'",
                (),
                "dp_admin is not in rbac.roles",
                id="rules-not-loaded",
            ),
        ],
    )
    async def test_fails_and_names_each_difference(
        self,
        rbac: Postgres,
        setup: str | None,
        extra_roles: tuple[str, ...],
        message: str,
    ) -> None:
        """A difference stops the job, and the error names it, so that a stale role never keeps a right."""
        await make_serving_login(rbac)
        if setup is not None:
            await rbac.connection.execute(setup.encode())

        with pytest.raises(psycopg.errors.RaiseException, match=re.escape(message)):
            async with rbac.connection.transaction():
                await verify_rbac(rbac, extra_roles)


FAULTS: Final[dict[str, Callable[[RbacRules], RbacRules]]] = {
    "unknown-parent": lambda rules: rules.changed(
        rules.roles[0].name, inherits=[*rules.roles[0].inherits, "dp_ghost"]
    ),
    "unknown-permission": lambda rules: rules.changed(
        rules.roles[0].name,
        permissions=[*rules.roles[0].permissions, "zzzzzz_zzzzzz"],
    ),
    "role-inherits-itself": lambda rules: rules.changed(
        rules.roles[0].name, inherits=[*rules.roles[0].inherits, rules.roles[0].name]
    ),
    "repeated-role": lambda rules: rules.model_copy(
        update={"roles": [*rules.roles, rules.roles[0]]}
    ),
    "two-roles-in-a-cycle": lambda rules: rules.model_copy(
        update={
            "roles": [
                *(
                    role
                    for role in rules.roles
                    if role.name not in {ROLE_POOL[6], ROLE_POOL[7]}
                ),
                RbacRole(
                    name=ROLE_POOL[6],
                    description="a",
                    inherits=[ROLE_POOL[7]],
                    permissions=[],
                ),
                RbacRole(
                    name=ROLE_POOL[7],
                    description="b",
                    inherits=[ROLE_POOL[6]],
                    permissions=[],
                ),
            ]
        }
    ),
}


@pytest.mark.postgres
class TestRbacProperties:
    """Random rules files and random names against a reference that the test computes in Python."""

    @PROPERTIES
    @given(rules=rbac_rules())
    def test_can_answers_the_closure_of_the_role_for_any_rules(
        self, rbac_connection: psycopg.Connection[TupleRow], rules: RbacRules
    ) -> None:
        """Every role of every random file is allowed exactly what it holds or inherits."""
        with rbac_connection.transaction(force_rollback=True):
            install_rbac_sync(rbac_connection, rules)

            for role in [*(role.name for role in rules.roles), "dp_ghost", ""]:
                expected = rules.effective(role)
                assert answers_for(rbac_connection, role, rules.features) == {
                    feature: feature in expected for feature in rules.features
                }

    @PROPERTIES
    @given(rules=rbac_rules(), seed=st.randoms(use_true_random=False))
    def test_the_stored_rules_do_not_depend_on_the_order_of_the_file(
        self,
        rbac_connection: psycopg.Connection[TupleRow],
        rules: RbacRules,
        seed: Random,
    ) -> None:
        """Shuffling the roles and their lists gives the same table."""
        shuffled = rules.model_copy(
            update={
                "roles": [
                    role.model_copy(
                        update={
                            "inherits": seed.sample(role.inherits, len(role.inherits)),
                            "permissions": seed.sample(
                                role.permissions, len(role.permissions)
                            ),
                        }
                    )
                    for role in seed.sample(rules.roles, len(rules.roles))
                ]
            }
        )
        with rbac_connection.transaction(force_rollback=True):
            install_rbac_sync(rbac_connection, rules)
            first = stored_roles_sync(rbac_connection)
            install_rbac_sync(rbac_connection, shuffled)

            assert stored_roles_sync(rbac_connection) == first

    @PROPERTIES
    @given(first=rbac_rules(), second=rbac_rules())
    def test_a_second_file_replaces_the_first_completely(
        self,
        rbac_connection: psycopg.Connection[TupleRow],
        first: RbacRules,
        second: RbacRules,
    ) -> None:
        """No role, and no permission, of the earlier file survives in the table."""
        with rbac_connection.transaction(force_rollback=True):
            install_rbac_sync(rbac_connection, first)
            install_rbac_sync(rbac_connection, second)

            assert stored_roles_sync(rbac_connection) == sorted(
                (role.name, sorted(second.effective(role.name)))
                for role in second.roles
            )

    @PROPERTIES
    @given(rules=rbac_rules(max_roles=6), fault=st.sampled_from(sorted(FAULTS)))
    def test_a_file_with_one_fault_is_refused_and_leaves_the_rules_unchanged(
        self,
        rbac_connection: psycopg.Connection[TupleRow],
        rules: RbacRules,
        fault: str,
    ) -> None:
        """Whatever the valid file around it, one fault fails the load and changes nothing."""
        with rbac_connection.transaction(force_rollback=True):
            install_rbac_sync(rbac_connection, rules)
            before = stored_roles_sync(rbac_connection)

            with pytest.raises(psycopg.Error), rbac_connection.transaction():
                install_rbac_sync(rbac_connection, FAULTS[fault](rules))

            assert stored_roles_sync(rbac_connection) == before

    @PROPERTIES
    @given(rules=rbac_rules(max_roles=8), data=st.data())
    def test_a_new_inherits_edge_never_removes_a_permission(
        self,
        rbac_connection: psycopg.Connection[TupleRow],
        rules: RbacRules,
        data: st.DataObject,
    ) -> None:
        """Letting a role inherit one more earlier role can only add permissions."""
        ordered = sorted(rules.roles, key=lambda role: role.name)
        if len(ordered) < 2:
            return
        child = data.draw(st.integers(min_value=1, max_value=len(ordered) - 1))
        parent = data.draw(st.integers(min_value=0, max_value=child - 1))
        more = rules.changed(
            ordered[child].name,
            inherits=[*ordered[child].inherits, ordered[parent].name],
        )
        with rbac_connection.transaction(force_rollback=True):
            install_rbac_sync(rbac_connection, rules)
            before = dict(stored_roles_sync(rbac_connection))
            install_rbac_sync(rbac_connection, more)
            after = dict(stored_roles_sync(rbac_connection))

        assert all(set(before[name]) <= set(after[name]) for name in before)

    @PROPERTIES
    @given(case=claim_and_schema())
    def test_mine_is_true_only_for_one_whole_element_of_the_claim(
        self, rbac_connection: psycopg.Connection[TupleRow], case: tuple[str, str]
    ) -> None:
        """Any text for the claim and for the schema: only a non-empty exact element matches."""
        claim, schema = case
        with rbac_connection.transaction(force_rollback=True):
            install_rbac_sync(rbac_connection, RBAC)
            rbac_connection.execute(
                "SELECT set_config('app.claim_schemas', %s, true)", (claim,)
            )
            rbac_connection.execute(b"SELECT rbac.load()")

            answer = rbac_connection.execute(
                "SELECT rbac.mine(%s)", (schema,)
            ).fetchone()

        elements = claim.split(",")
        assert answer == (
            bool(schema)
            and "," not in schema
            and (schema in elements or "*" in elements or f"{schema}:*" in elements),
        )

    @PROPERTIES
    @given(
        role=st.sampled_from([*(role.name for role in RBAC.roles), "dp_ghost"]),
        feature=st.one_of(
            st.sampled_from(RBAC.features),
            st.sampled_from(RBAC.features).map(operator.itemgetter(slice(-1))),
            st.lists(st.sampled_from(RBAC.features), min_size=2, max_size=3).map(
                ",".join
            ),
            st.text(
                alphabet=st.characters(
                    blacklist_categories=("Cs",), blacklist_characters="\x00"
                ),
                max_size=12,
            ),
            st.just(""),
            st.none(),
        ),
    )
    def test_can_allows_only_a_held_permission_and_raises_for_any_other_name(
        self,
        rbac_connection: psycopg.Connection[TupleRow],
        role: str,
        feature: str | None,
    ) -> None:
        """A name that the file lists gives true or false by the role. Any other text, a comma list, empty or null raises."""
        listed = feature in RBAC.features
        with rbac_connection.transaction(force_rollback=True):
            install_rbac_sync(rbac_connection, RBAC)
            rbac_connection.execute(
                "SELECT set_config('app.claim_role', %s, true)", (role,)
            )
            rbac_connection.execute(b"SELECT rbac.load()")

            if listed:
                answer = rbac_connection.execute(
                    "SELECT rbac.can(%s)", (feature,)
                ).fetchone()
                assert answer == (feature in RBAC.effective(role),)
            else:
                with (
                    pytest.raises(psycopg.Error) as error,
                    rbac_connection.transaction(),
                ):
                    rbac_connection.execute("SELECT rbac.can(%s)", (feature,))
                assert error.value.sqlstate == "PT500"
