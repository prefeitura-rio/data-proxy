import pytest
from httpx2 import AsyncClient, Response

from helpers import ProxyAdmin, document, table

TEST_SCHEMA: dict[str, object] = {
    "source": {"type": "bigquery"},
    "claim": "preferred_username",
    "ducklake": {"encrypted": False},
    "tables": {"full_table": table("proj.ds.full_table")},
}
NEW_SCHEMA: dict[str, object] = {
    "source": {"type": "bigquery"},
    "claim": "preferred_username",
    "ducklake": {"encrypted": False},
    "tables": {"orders": table("proj.ds.orders")},
}


async def seed(api: AsyncClient) -> None:
    """Store a config with the schema test and its table full_table."""
    stored = await api.put("/config", json=document(), headers={"If-Match": '"0"'})
    assert stored.status_code == 200


async def revision_of(api: AsyncClient) -> int | None:
    """Read the id of the active revision."""
    body: dict[str, dict[str, int] | None] = (await api.get("/config")).json()
    revision = body["revision"]
    return None if revision is None else revision["id"]


def problem_pointers(response: Response) -> list[str]:
    """List the JSON Pointers of a problem answer."""
    return [item["pointer"] for item in response.json()["errors"]]


@pytest.mark.postgres
class TestReadSchemas:
    """Read the schemas and their tables."""

    async def test_lists_the_schemas_with_their_tables(self, api: AsyncClient) -> None:
        """Show each schema by name with the names of its tables."""
        await seed(api)

        response = await api.get("/schemas")

        assert response.status_code == 200
        assert response.json() == [{"name": "test", "tables": ["full_table"]}]

    async def test_shows_one_schema_with_an_etag_that_follows_its_content(
        self, api: AsyncClient
    ) -> None:
        """Return the schema document, and the same ETag until the schema changes."""
        await seed(api)

        first = await api.get("/schemas/test")
        second = await api.get("/schemas/test")

        assert first.status_code == 200
        assert first.json() == TEST_SCHEMA
        assert first.headers["etag"] == second.headers["etag"]
        assert first.headers["etag"].startswith('"')

    @pytest.mark.parametrize(
        "path",
        [
            pytest.param("/schemas/missing", id="schema"),
            pytest.param("/schemas/missing/tables", id="tables-of-a-schema"),
            pytest.param("/schemas/test/tables/missing", id="table"),
        ],
    )
    async def test_answers_404_for_what_does_not_exist(
        self, api: AsyncClient, path: str
    ) -> None:
        """Say that the resource is missing, as a problem document."""
        await seed(api)

        response = await api.get(path)

        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/problem+json")

    async def test_shows_the_tables_and_one_table(self, api: AsyncClient) -> None:
        """Return the tables of a schema as a map, and one table with its own ETag."""
        await seed(api)

        tables = await api.get("/schemas/test/tables")
        one = await api.get("/schemas/test/tables/full_table")

        assert tables.json() == {"full_table": table("proj.ds.full_table")}
        assert one.json() == table("proj.ds.full_table")
        assert one.headers["etag"]


@pytest.mark.postgres
class TestWriteSchemas:
    """Create, change and remove a schema."""

    async def test_creates_a_schema_and_stores_a_revision(
        self, api: AsyncClient
    ) -> None:
        """Answer the schema with its ETag, and keep who wrote it and why."""
        await seed(api)

        response = await api.put(
            "/schemas/other?comment=add+other",
            json=NEW_SCHEMA,
            headers={"If-None-Match": "*", "X-Forwarded-User": "ana"},
        )

        assert response.status_code == 200
        assert response.json() == NEW_SCHEMA
        assert (
            response.headers["etag"]
            == (await api.get("/schemas/other")).headers["etag"]
        )
        assert response.headers["x-revision"] == "2"
        revisions = (await api.get("/config/revisions")).json()
        assert (
            revisions[0]["id"],
            revisions[0]["author"],
            revisions[0]["comment"],
        ) == (
            2,
            "ana",
            "add other",
        )
        assert sorted(item["name"] for item in (await api.get("/schemas")).json()) == [
            "other",
            "test",
        ]

    @pytest.mark.parametrize(
        ("headers", "status"),
        [
            pytest.param({}, 428, id="no-precondition"),
            pytest.param({"If-None-Match": "*"}, 412, id="create-over-an-existing-one"),
            pytest.param({"If-Match": '"stale"'}, 412, id="stale-etag"),
        ],
    )
    async def test_refuses_a_write_with_a_wrong_precondition(
        self, api: AsyncClient, headers: dict[str, str], status: int
    ) -> None:
        """Answer 428 or 412, and store nothing."""
        await seed(api)

        response = await api.put("/schemas/test", json=NEW_SCHEMA, headers=headers)

        assert response.status_code == status
        assert await revision_of(api) == 1

    async def test_replaces_a_schema_with_its_current_etag(
        self, api: AsyncClient
    ) -> None:
        """Replace the whole schema when the ETag is the one that the caller read."""
        await seed(api)
        etag = (await api.get("/schemas/test")).headers["etag"]

        response = await api.put(
            "/schemas/test", json=NEW_SCHEMA, headers={"If-Match": etag}
        )

        assert response.status_code == 200
        assert (await api.get("/schemas/test")).json() == NEW_SCHEMA
        assert response.headers["etag"] != etag

    async def test_a_change_elsewhere_does_not_make_the_etag_stale(
        self, api: AsyncClient
    ) -> None:
        """Let two callers change two schemas in turn with the ETags that they read first."""
        await seed(api)
        await api.put("/schemas/other", json=NEW_SCHEMA, headers={"If-None-Match": "*"})
        test_etag = (await api.get("/schemas/test")).headers["etag"]
        other_etag = (await api.get("/schemas/other")).headers["etag"]

        first = await api.patch(
            "/schemas/test", json={"claim": "sub"}, headers={"If-Match": test_etag}
        )
        second = await api.patch(
            "/schemas/other", json={"claim": "sub"}, headers={"If-Match": other_etag}
        )

        assert (first.status_code, second.status_code) == (200, 200)

    async def test_patches_a_schema_and_keeps_its_tables(
        self, api: AsyncClient
    ) -> None:
        """Merge the patch into the schema, and remove a key set to null."""
        await seed(api)
        etag = (await api.get("/schemas/test")).headers["etag"]

        response = await api.patch(
            "/schemas/test", json={"claim": "sub"}, headers={"If-Match": etag}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["claim"] == "sub"
        assert body["tables"] == {"full_table": table("proj.ds.full_table")}

    async def test_removes_a_schema(self, api: AsyncClient) -> None:
        """Answer 204, and show the schema as gone."""
        await seed(api)
        etag = (await api.get("/schemas/test")).headers["etag"]

        response = await api.delete("/schemas/test", headers={"If-Match": etag})

        assert response.status_code == 204
        assert (await api.get("/schemas/test")).status_code == 404
        assert await revision_of(api) == 2

    @pytest.mark.parametrize("method", ["PATCH", "DELETE"])
    async def test_answers_404_when_it_changes_a_schema_that_does_not_exist(
        self, api: AsyncClient, method: str
    ) -> None:
        """Say that the schema is missing, and store nothing."""
        await seed(api)

        response = await api.request(
            method,
            "/schemas/missing",
            json={} if method == "PATCH" else None,
            headers={"If-Match": '"x"'},
        )

        assert response.status_code == 404
        assert await revision_of(api) == 1

    async def test_refuses_a_schema_that_the_engine_refuses(
        self, api: AsyncClient
    ) -> None:
        """Answer 422 with a pointer to the problem, and store nothing."""
        await seed(api)
        bad = {**NEW_SCHEMA, "tables": {"wrong": table("proj.ds.orders")}}

        response = await api.put(
            "/schemas/other", json=bad, headers={"If-None-Match": "*"}
        )

        assert response.status_code == 422
        assert response.headers["content-type"].startswith("application/problem+json")
        assert "/schemas/other/tables/wrong" in problem_pointers(response)
        assert await revision_of(api) == 1

    async def test_checks_a_write_without_storing_it(self, api: AsyncClient) -> None:
        """Report what would change, and keep the revision."""
        await seed(api)

        response = await api.put(
            "/schemas/other?dry_run=true",
            json=NEW_SCHEMA,
            headers={"If-None-Match": "*"},
        )

        assert response.status_code == 200
        assert response.json() == {
            "valid": True,
            "errors": [],
            "changes": [{"pointer": "/schemas/other", "change": "added"}],
        }
        assert await revision_of(api) == 1
        assert (await api.get("/schemas/other")).status_code == 404

    async def test_tells_the_proxies_to_reload(
        self, api: AsyncClient, proxy_admin: ProxyAdmin
    ) -> None:
        """Call the reload endpoint after a write, because a table can change its cache time."""
        await seed(api)
        proxy_admin.calls.clear()

        await api.put("/schemas/other", json=NEW_SCHEMA, headers={"If-None-Match": "*"})

        assert proxy_admin.calls == ["/reload"]


@pytest.mark.postgres
class TestWriteTables:
    """Create, change and remove one table."""

    async def test_creates_a_table_in_a_schema(self, api: AsyncClient) -> None:
        """Add the table to its schema, and show it with its own ETag."""
        await seed(api)

        response = await api.put(
            "/schemas/test/tables/orders",
            json=table("proj.ds.orders", cache_ttl=60),
            headers={"If-None-Match": "*"},
        )

        assert response.status_code == 200
        assert response.json() == table("proj.ds.orders", cache_ttl=60)
        tables = (await api.get("/schemas/test/tables")).json()
        assert sorted(tables) == ["full_table", "orders"]
        assert (
            response.headers["etag"]
            == (await api.get("/schemas/test/tables/orders")).headers["etag"]
        )

    async def test_patches_one_table_and_leaves_the_others(
        self, api: AsyncClient
    ) -> None:
        """Change one field of one table."""
        await seed(api)
        etag = (await api.get("/schemas/test/tables/full_table")).headers["etag"]

        response = await api.patch(
            "/schemas/test/tables/full_table",
            json={"cache_ttl": 30},
            headers={"If-Match": etag},
        )

        assert response.status_code == 200
        assert response.json() == table("proj.ds.full_table", cache_ttl=30)

    async def test_a_change_in_another_table_does_not_make_the_etag_stale(
        self, api: AsyncClient
    ) -> None:
        """Change two tables of one schema with the ETags that the caller read first."""
        await seed(api)
        await api.put(
            "/schemas/test/tables/orders",
            json=table("proj.ds.orders"),
            headers={"If-None-Match": "*"},
        )
        full = (await api.get("/schemas/test/tables/full_table")).headers["etag"]
        orders = (await api.get("/schemas/test/tables/orders")).headers["etag"]

        first = await api.patch(
            "/schemas/test/tables/full_table",
            json={"cache_ttl": 1},
            headers={"If-Match": full},
        )
        second = await api.patch(
            "/schemas/test/tables/orders",
            json={"cache_ttl": 2},
            headers={"If-Match": orders},
        )

        assert (first.status_code, second.status_code) == (200, 200)

    async def test_removes_a_table_from_the_config(self, api: AsyncClient) -> None:
        """Answer 204, and keep the data, because the config isn't the data."""
        await seed(api)
        etag = (await api.get("/schemas/test/tables/full_table")).headers["etag"]

        response = await api.delete(
            "/schemas/test/tables/full_table", headers={"If-Match": etag}
        )

        assert response.status_code == 204
        assert (await api.get("/schemas/test/tables/full_table")).status_code == 404
        assert (await api.get("/schemas/test/tables")).json() == {}

    async def test_refuses_a_key_that_is_not_the_table_name(
        self, api: AsyncClient
    ) -> None:
        """Answer 422 with a pointer to the table, and store nothing."""
        await seed(api)

        response = await api.put(
            "/schemas/test/tables/wrong",
            json=table("proj.ds.orders"),
            headers={"If-None-Match": "*"},
        )

        assert response.status_code == 422
        assert "/schemas/test/tables/wrong" in problem_pointers(response)
        assert await revision_of(api) == 1

    async def test_answers_404_when_the_schema_does_not_exist(
        self, api: AsyncClient
    ) -> None:
        """Say that the schema is missing, and create nothing."""
        await seed(api)

        response = await api.put(
            "/schemas/missing/tables/orders",
            json=table("proj.ds.orders"),
            headers={"If-None-Match": "*"},
        )

        assert response.status_code == 404
        assert await revision_of(api) == 1
