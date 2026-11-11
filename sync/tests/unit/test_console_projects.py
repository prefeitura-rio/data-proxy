import pytest

from tests.fixtures.console import as_token, refusal, use_database_role
from tests.fixtures.types import Postgres
from tests.helpers.console import (
    ADD_PROJECT,
    ADD_SCHEMA,
    add_projects,
    add_schemas,
    load_claims,
)
from tests.helpers.core import names

CHECK_VIOLATION = "23514"
FOREIGN_KEY_VIOLATION = "23503"
UNIQUE_VIOLATION = "23505"
RESTRICT_VIOLATION = "23001"
INSUFFICIENT = "42501"


@pytest.mark.postgres
class TestProjectNames:
    """A schema has one name in the database: its short name, with the prefix of its project unless the project is default."""

    @pytest.mark.parametrize(
        ("project", "short_name", "name"),
        [
            pytest.param(
                "sales", "orders", "sales_orders", id="a-project-adds-its-prefix"
            ),
            pytest.param("default", "orders", "orders", id="default-adds-no-prefix"),
            pytest.param(
                "sales", "a_b", "sales_a_b", id="the-short-name-keeps-its-underscores"
            ),
        ],
    )
    async def test_the_name_comes_from_the_project_and_the_short_name(
        self, console_api: Postgres, project: str, short_name: str, name: str
    ) -> None:
        """Let the database build the name that the sync, the proxy and the roles use."""
        await add_projects(console_api, "sales")

        await add_schemas(console_api, (project, short_name))

        assert await names(console_api, "SELECT name FROM console.schemas") == [name]

    async def test_a_name_with_no_project_belongs_to_default(
        self, console_api: Postgres
    ) -> None:
        """Keep the old insert, which gives only a name, working."""
        await console_api.connection.execute(
            b"INSERT INTO console.schemas (name) VALUES ('hr')"
        )

        cursor = await console_api.connection.execute(
            b"SELECT name, project, short_name FROM console.schemas"
        )

        assert await cursor.fetchall() == [("hr", "default", "hr")]

    @pytest.mark.parametrize(
        ("project", "state"),
        [
            pytest.param("sales2", None, id="letters-and-digits"),
            pytest.param("a_b", CHECK_VIOLATION, id="underscore"),
            pytest.param("Sales", CHECK_VIOLATION, id="upper-case"),
            pytest.param("1sales", CHECK_VIOLATION, id="starts-with-a-digit"),
            pytest.param("a" * 30, None, id="30-characters"),
            pytest.param("a" * 31, CHECK_VIOLATION, id="31-characters"),
            pytest.param("default", UNIQUE_VIOLATION, id="default-exists"),
        ],
    )
    async def test_a_project_name_has_letters_and_digits_only(
        self, console_api: Postgres, project: str, state: str | None
    ) -> None:
        """Keep the first underscore of a name the end of the project, so a name has one reading."""
        assert await refusal(console_api, ADD_PROJECT, project) == state

    @pytest.mark.parametrize(
        ("project", "short_name", "state"),
        [
            pytest.param("sales", "orders", None, id="a-valid-schema"),
            pytest.param(
                "missing", "orders", FOREIGN_KEY_VIOLATION, id="unknown-project"
            ),
            pytest.param("sales", "x" * 50, None, id="name-of-56-characters"),
            pytest.param(
                "sales", "x" * 51, CHECK_VIOLATION, id="name-of-57-characters"
            ),
            pytest.param(
                "sales", "Orders", CHECK_VIOLATION, id="upper-case-short-name"
            ),
            pytest.param(
                "sales", "pg_data", None, id="a-prefix-reserves-the-whole-name-only"
            ),
            pytest.param("default", "console", CHECK_VIOLATION, id="reserved-name"),
            pytest.param("default", "pg_data", CHECK_VIOLATION, id="reserved-prefix"),
        ],
    )
    async def test_the_schema_checks_follow_the_whole_name(
        self,
        console_api: Postgres,
        project: str,
        short_name: str,
        state: str | None,
    ) -> None:
        """Apply the rules of the schema name to the name that the project makes."""
        await add_projects(console_api, "sales")

        assert await refusal(console_api, ADD_SCHEMA, project, short_name) == state

    async def test_two_schemas_never_share_a_name(self, console_api: Postgres) -> None:
        """Refuse `a_b` of default and `b` of project a, because both make `a_b`."""
        await add_projects(console_api, "a")
        await add_schemas(console_api, ("default", "a_b"))

        assert await refusal(console_api, ADD_SCHEMA, "a", "b") == UNIQUE_VIOLATION

    async def test_a_schema_cannot_change_its_project_or_its_short_name(
        self, console_api: Postgres
    ) -> None:
        """Make a rename the same as a delete and an add, because the physical schema follows the name."""
        await add_projects(console_api, "sales", "hr")
        await add_schemas(console_api, ("sales", "orders"))

        move = "UPDATE console.schemas SET project = 'hr' WHERE name = 'sales_orders'"
        rename = "UPDATE console.schemas SET short_name = 'sales' WHERE name = 'sales_orders'"
        same = "UPDATE console.schemas SET short_name = short_name WHERE name = 'sales_orders'"

        assert await refusal(console_api, move) == CHECK_VIOLATION
        assert await refusal(console_api, rename) == CHECK_VIOLATION
        assert await refusal(console_api, same) is None

    async def test_a_project_with_schemas_cannot_be_deleted(
        self, console_api: Postgres
    ) -> None:
        """Keep a delete of a project from reaching the data of its schemas."""
        await add_projects(console_api, "sales", "empty")
        await add_schemas(console_api, ("sales", "orders"))

        full = "DELETE FROM console.projects WHERE name = 'sales'"
        empty = "DELETE FROM console.projects WHERE name = 'empty'"

        assert await refusal(console_api, full) == RESTRICT_VIOLATION
        assert await refusal(console_api, empty) is None


@pytest.mark.postgres
@pytest.mark.usefixtures("seed")
class TestProjectClaim:
    """A token with the projects claim reaches every schema of its projects, also a schema that comes later."""

    @pytest.fixture
    async def seed(self, console_api: Postgres) -> None:
        """Make two projects with schemas, and a schema of default."""
        await add_projects(console_api, "sales", "hr")
        await add_schemas(
            console_api,
            ("sales", "orders"),
            ("sales", "crm"),
            ("hr", "staff"),
            ("default", "legacy"),
        )

    @pytest.mark.parametrize(
        ("schemas", "projects", "expected"),
        [
            pytest.param("", "", "", id="no-claim"),
            pytest.param("legacy", "", ",legacy,", id="schemas-claim-only"),
            pytest.param("", "sales", ",sales_crm,sales_orders,", id="a-project"),
            pytest.param(
                "",
                "sales:*",
                ",sales_crm:*,sales_orders:*,",
                id="all-rows-of-a-project",
            ),
            pytest.param(
                "", "sales,hr", ",hr_staff,sales_crm,sales_orders,", id="two-projects"
            ),
            pytest.param(
                "legacy", "hr", ",legacy,hr_staff,", id="schemas-and-projects"
            ),
            pytest.param("", "default", ",legacy,", id="the-default-project"),
            pytest.param("", "missing", "", id="an-unknown-project"),
            pytest.param("", "*", ",*,", id="every-project"),
            pytest.param("", "sale", "", id="a-prefix-is-not-a-project"),
        ],
    )
    async def test_the_projects_claim_expands_into_schemas(
        self, console_api: Postgres, schemas: str, projects: str, expected: str
    ) -> None:
        """Let `rbac.mine` and every view read one list, whatever claim gave the schemas."""
        assert (
            await load_claims(console_api, schemas=schemas, projects=projects)
            == expected
        )

    async def test_a_schema_that_comes_later_is_in_the_next_expansion(
        self, console_api: Postgres
    ) -> None:
        """Give the project a new schema and read it with the same claim."""
        before = await load_claims(console_api, projects="hr")
        await add_schemas(console_api, ("hr", "payroll"))

        assert before == ",hr_staff,"
        assert await load_claims(console_api, projects="hr") == ",hr_payroll,hr_staff,"

    @pytest.mark.parametrize(
        ("claim", "reads"),
        [
            pytest.param("sales", ["sales_crm", "sales_orders"], id="its-project"),
            pytest.param("hr", ["hr_staff"], id="another-project"),
            pytest.param("sales:*", ["sales_crm", "sales_orders"], id="all-rows"),
            pytest.param("none", [], id="no-project"),
        ],
    )
    async def test_a_project_token_reads_the_schemas_of_the_project_only(
        self, console_api: Postgres, claim: str, reads: list[str]
    ) -> None:
        """Run the row policies of the console tables on the expanded claim."""
        await load_claims(console_api, projects=claim)
        await use_database_role(console_api)

        assert (
            await names(console_api, "SELECT name FROM console.schemas ORDER BY name")
            == reads
        )

    @pytest.mark.parametrize(
        ("role", "projects", "project", "state"),
        [
            pytest.param(
                "dp_admin",
                ["sales"],
                "sales",
                None,
                id="a-schema-that-does-not-exist-yet",
            ),
            pytest.param(
                "dp_admin", ["sales"], "hr", INSUFFICIENT, id="another-project"
            ),
            pytest.param(
                "dp_admin", ["sales:*"], "sales", None, id="all-rows-of-the-project"
            ),
            pytest.param("dp_admin", ["*"], "hr", None, id="every-project"),
            pytest.param(
                "dp_console",
                ["sales"],
                "sales",
                INSUFFICIENT,
                id="no-create-permission",
            ),
            pytest.param("dp_admin", [], "sales", INSUFFICIENT, id="no-claim"),
        ],
    )
    async def test_a_token_adds_a_schema_in_the_projects_of_its_claim(
        self,
        console_api: Postgres,
        role: str,
        projects: list[str],
        project: str,
        state: str | None,
    ) -> None:
        """Let the row policy of `schemas` ask for the project of the new row."""
        await as_token(console_api, role, [], projects=projects, database_role=True)

        assert await refusal(console_api, ADD_SCHEMA, project, "ledger") == state


@pytest.mark.postgres
@pytest.mark.usefixtures("seed")
class TestProjectRows:
    """A token sees and changes the project rows of its claim."""

    @pytest.fixture
    async def seed(self, console_api: Postgres) -> None:
        """Make two projects with a schema each, and an empty project."""
        await add_projects(console_api, "sales", "hr", "empty")
        await add_schemas(console_api, ("sales", "orders"), ("hr", "staff"))

    @pytest.mark.parametrize(
        ("schemas", "projects", "reads"),
        [
            pytest.param([], ["sales"], ["sales"], id="by-the-project-claim"),
            pytest.param(["hr_staff"], [], ["hr"], id="by-a-schema-of-the-project"),
            pytest.param([], [], [], id="no-claim"),
            pytest.param(
                [], ["*"], ["default", "empty", "hr", "sales"], id="every-project"
            ),
        ],
    )
    async def test_a_token_reads_a_project_by_its_claim_or_by_one_of_its_schemas(
        self,
        console_api: Postgres,
        schemas: list[str],
        projects: list[str],
        reads: list[str],
    ) -> None:
        """Show a user the project of every schema that the user reaches."""
        await as_token(
            console_api, "dp_admin", schemas, projects=projects, database_role=True
        )

        assert (
            await names(console_api, "SELECT name FROM console.projects ORDER BY name")
            == reads
        )

    @pytest.mark.parametrize(
        ("role", "projects", "state"),
        [
            pytest.param("dp_admin", ["new"], None, id="a-project-of-the-claim"),
            pytest.param(
                "dp_admin", ["other"], INSUFFICIENT, id="a-project-outside-the-claim"
            ),
            pytest.param("dp_admin", ["*"], None, id="every-project"),
            pytest.param(
                "dp_console", ["new"], INSUFFICIENT, id="no-create-permission"
            ),
        ],
    )
    async def test_a_token_adds_a_project_only_inside_its_claim(
        self, console_api: Postgres, role: str, projects: list[str], state: str | None
    ) -> None:
        """Let the claim name a project that does not exist yet, as it does for a schema."""
        await as_token(console_api, role, [], projects=projects, database_role=True)

        assert await refusal(console_api, ADD_PROJECT, "new") == state

    async def test_the_global_wildcard_of_the_schemas_claim_adds_a_project(
        self, console_api: Postgres
    ) -> None:
        """Keep a token that reaches every schema able to add a project, as it was able to add a schema."""
        await as_token(console_api, "dp_admin", ["*"], database_role=True)

        assert await refusal(console_api, ADD_PROJECT, "new") is None

    @pytest.mark.parametrize(
        ("role", "projects", "deleted"),
        [
            pytest.param("dp_admin", ["empty"], 1, id="a-project-of-the-claim"),
            pytest.param("dp_admin", ["hr"], 0, id="a-project-outside-the-claim"),
            pytest.param("dp_console", ["empty"], 0, id="no-delete-permission"),
        ],
    )
    async def test_a_token_deletes_a_project_of_its_claim_with_permission(
        self,
        console_api: Postgres,
        role: str,
        projects: list[str],
        deleted: int,
    ) -> None:
        """A row policy that hides the row removes nothing, and the answer is not an error."""
        await as_token(console_api, role, [], projects=projects, database_role=True)

        cursor = await console_api.connection.execute(
            b"DELETE FROM console.projects WHERE name = 'empty'"
        )

        assert cursor.rowcount == deleted
