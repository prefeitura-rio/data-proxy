import pytest
from httpx2 import AsyncClient

from console.settings import settings
from helpers import ProxyAdmin, document, table


@pytest.mark.postgres
class TestProxyReload:
    """Tell the proxies to reload when the sync config changes."""

    async def test_reloads_the_proxies_after_a_write_and_a_patch(
        self, api: AsyncClient, proxy_admin: ProxyAdmin
    ) -> None:
        """Call the reload endpoint once for each change."""
        written = await api.put("/config", json=document(), headers={"If-Match": '"0"'})
        assert written.status_code == 200
        assert proxy_admin.calls == ["/reload"]

        patched = await api.patch(
            "/config",
            json={"schemas": {"test": {"claim": "other"}}},
            headers={"If-Match": '"1"'},
        )

        assert patched.status_code == 200
        assert proxy_admin.calls == ["/reload", "/reload"]

    async def test_reloads_the_proxies_after_a_rollback(
        self, api: AsyncClient, proxy_admin: ProxyAdmin
    ) -> None:
        """Call the reload endpoint when an older revision becomes active."""
        await api.put("/config", json=document(), headers={"If-Match": '"0"'})
        other = document({"x": table("proj.ds.x")})
        await api.put("/config", json=other, headers={"If-Match": '"1"'})
        proxy_admin.calls.clear()

        response = await api.post(
            "/config/revisions/1/activate", headers={"If-Match": '"2"'}
        )

        assert response.status_code == 200
        assert proxy_admin.calls == ["/reload"]

    async def test_leaves_the_proxies_alone_for_a_dry_run(
        self, api: AsyncClient, proxy_admin: ProxyAdmin
    ) -> None:
        """Call nothing when nothing is written."""
        await api.put(
            "/config?dry_run=true", json=document(), headers={"If-Match": '"0"'}
        )

        assert proxy_admin.calls == []

    async def test_a_proxy_that_cannot_be_reached_does_not_fail_the_write(
        self, api: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Answer the write, and let the proxy catch up at its next check."""
        monkeypatch.setattr(settings, "PROXY_ADMIN_URL", "http://127.0.0.1:1/reload")

        response = await api.put(
            "/config", json=document(), headers={"If-Match": '"0"'}
        )

        assert response.status_code == 200
