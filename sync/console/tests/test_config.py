import pytest
from httpx2 import AsyncClient

from helpers import document, table


async def put(
    api: AsyncClient,
    body: dict[str, object],
    etag: str = '"0"',
    query: str = "",
    author: str = "ana",
) -> int:
    """Store a config and return the status."""
    response = await api.put(
        f"/config{query}",
        json=body,
        headers={"If-Match": etag, "X-Forwarded-User": author},
    )
    return response.status_code


@pytest.mark.postgres
class TestConfig:
    """Read and write the sync config as a document with revisions."""

    async def test_starts_empty_with_the_first_etag(self, api: AsyncClient) -> None:
        """Show an empty document before the first write."""
        response = await api.get("/config")

        assert response.status_code == 200
        assert response.json() == {"revision": None, "document": {"schemas": {}}}
        assert response.headers["etag"] == '"0"'

    async def test_stores_the_first_config_and_returns_the_new_etag(
        self, api: AsyncClient
    ) -> None:
        """Keep the document, who wrote it and why, and move the ETag."""
        response = await api.put(
            "/config?comment=first+import",
            json=document(),
            headers={"If-Match": '"0"', "X-Forwarded-User": "ana"},
        )

        assert response.status_code == 200
        assert response.headers["etag"] == '"1"'
        body = response.json()
        assert body["document"] == document()
        assert (body["revision"]["id"], body["revision"]["author"]) == (1, "ana")
        assert body["revision"]["comment"] == "first import"
        assert (await api.get("/config")).json() == body

    async def test_requires_if_match_on_a_write(self, api: AsyncClient) -> None:
        """Answer 428 when the caller doesn't say which config it read."""
        response = await api.put("/config", json=document())

        assert response.status_code == 428

    async def test_rejects_a_write_over_a_newer_config(self, api: AsyncClient) -> None:
        """Answer 412 when somebody else wrote since the caller read."""
        assert await put(api, document()) == 200

        assert await put(api, document(), etag='"0"') == 412

    @pytest.mark.parametrize(
        ("config", "pointer"),
        [
            pytest.param(
                document({"wrong": table("proj.ds.full_table")}),
                "/schemas/test/tables/wrong",
                id="key-is-not-the-table-name",
            ),
            pytest.param(
                document({"full_table": table("proj.ds.full_table", strategy="nope")}),
                "/schemas/test/tables/full_table/strategy",
                id="unknown-strategy",
            ),
            pytest.param(
                document(schema="Bad-Name"),
                "/schemas/Bad-Name",
                id="bad-schema-name",
            ),
        ],
    )
    async def test_rejects_an_invalid_config_with_the_path_of_the_problem(
        self, api: AsyncClient, config: dict[str, object], pointer: str
    ) -> None:
        """Answer 422 with a pointer to each problem, and store nothing."""
        response = await api.put("/config", json=config, headers={"If-Match": '"0"'})

        assert response.status_code == 422
        assert response.headers["content-type"].startswith("application/problem+json")
        assert pointer in [error["pointer"] for error in response.json()["errors"]]
        assert (await api.get("/config")).json()["revision"] is None

    async def test_checks_a_config_without_writing_it_when_asked(
        self, api: AsyncClient
    ) -> None:
        """Report validity and the changes, and leave the stored config alone."""
        assert await put(api, document()) == 200
        bigger = document(
            {
                "full_table": table("proj.ds.full_table"),
                "other": table("proj.ds.other"),
            }
        )

        response = await api.put(
            "/config?dry_run=true", json=bigger, headers={"If-Match": '"1"'}
        )

        assert response.status_code == 200
        assert response.json() == {
            "valid": True,
            "errors": [],
            "changes": [{"pointer": "/schemas/test/tables/other", "change": "added"}],
        }
        assert (await api.get("/config")).headers["etag"] == '"1"'

    async def test_reports_the_problems_of_an_invalid_dry_run(
        self, api: AsyncClient
    ) -> None:
        """Answer 200 with valid false, because the check itself worked."""
        response = await api.put(
            "/config?dry_run=true",
            json=document({"wrong": table("proj.ds.full_table")}),
            headers={"If-Match": '"0"'},
        )

        assert response.status_code == 200
        assert response.json()["valid"] is False
        assert response.json()["errors"]

    async def test_patch_changes_one_table_and_keeps_the_others(
        self, api: AsyncClient
    ) -> None:
        """Merge a partial document into the stored one."""
        two = document({"a": table("proj.ds.a"), "b": table("proj.ds.b")})
        assert await put(api, two) == 200

        response = await api.patch(
            "/config",
            json={"schemas": {"test": {"tables": {"b": {"cache_ttl": 60}}}}},
            headers={"If-Match": '"1"'},
        )

        assert response.status_code == 200
        assert response.headers["etag"] == '"2"'
        tables: dict[str, dict[str, object]] = response.json()["document"]["schemas"][
            "test"
        ]["tables"]
        assert tables["b"]["cache_ttl"] == 60
        assert tables["a"] == table("proj.ds.a")

    async def test_patch_removes_what_the_patch_sets_to_null(
        self, api: AsyncClient
    ) -> None:
        """Delete a table by setting it to null."""
        two = document({"a": table("proj.ds.a"), "b": table("proj.ds.b")})
        assert await put(api, two) == 200

        response = await api.patch(
            "/config",
            json={"schemas": {"test": {"tables": {"a": None}}}},
            headers={"If-Match": '"1"'},
        )

        tables: dict[str, dict[str, object]] = response.json()["document"]["schemas"][
            "test"
        ]["tables"]
        assert list(tables) == ["b"]

    async def test_lists_the_revisions_newest_first(self, api: AsyncClient) -> None:
        """Show every revision, which one is active, and who wrote it."""
        assert await put(api, document(), author="ana") == 200
        assert await put(api, document(), etag='"1"', author="bob") == 200

        response = await api.get("/config/revisions")

        assert response.status_code == 200
        assert [
            (item["id"], item["author"], item["active"]) for item in response.json()
        ] == [(2, "bob", True), (1, "ana", False)]

    async def test_shows_one_revision_with_its_document(self, api: AsyncClient) -> None:
        """Return the stored document of one revision, or 404."""
        assert await put(api, document()) == 200

        found = await api.get("/config/revisions/1")
        missing = await api.get("/config/revisions/99")

        assert found.json()["document"] == document()
        assert missing.status_code == 404

    async def test_activates_an_older_revision(self, api: AsyncClient) -> None:
        """Roll back by moving the active flag, and keep the history."""
        assert await put(api, document()) == 200
        other = document({"other": table("proj.ds.other")})
        assert await put(api, other, etag='"1"') == 200

        response = await api.post(
            "/config/revisions/1/activate", headers={"If-Match": '"2"'}
        )

        assert response.status_code == 200
        assert response.headers["etag"] == '"1"'
        assert response.json()["document"] == document()
        assert await put(api, other, etag='"1"') == 200
        revisions = (await api.get("/config/revisions")).json()
        assert [item["id"] for item in revisions] == [3, 2, 1]

    async def test_activation_needs_the_current_etag(self, api: AsyncClient) -> None:
        """Answer 412 for a stale activation, and 404 for an unknown revision."""
        assert await put(api, document()) == 200

        stale = await api.post(
            "/config/revisions/1/activate", headers={"If-Match": '"0"'}
        )
        unknown = await api.post(
            "/config/revisions/99/activate", headers={"If-Match": '"1"'}
        )

        assert (stale.status_code, unknown.status_code) == (412, 404)
