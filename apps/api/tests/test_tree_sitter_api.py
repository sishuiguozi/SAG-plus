from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest


class FakeTreeSitterManager:
    def __init__(self) -> None:
        self.actions: list[str] = []

    def status(self):
        from sag_api.schemas.tree_sitter import TreeSitterResourceStatus

        return TreeSitterResourceStatus(
            version="1.13.7",
            state="missing",
            installed_languages=0,
            total_languages=306,
            downloaded_bytes=0,
            total_bytes=360 * 1024 * 1024,
            disk_bytes=0,
            progress=0,
        )

    async def start_download(self):
        self.actions.append("download")

    async def pause(self):
        self.actions.append("pause")

    async def resume(self):
        self.actions.append("resume")

    async def repair(self):
        self.actions.append("repair")


@pytest.mark.asyncio
async def test_tree_sitter_resource_endpoints_require_auth_and_delegate():
    from sag_api.core.deps import get_current_user
    from sag_api.main import app

    manager = FakeTreeSitterManager()
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        app.state.tree_sitter_manager = manager
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            denied = await client.get("/api/v1/system/tree-sitter")
            assert denied.status_code == 401

            app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="test-user")
            try:
                status = await client.get("/api/v1/system/tree-sitter")
                assert status.status_code == 200
                assert status.json()["total_languages"] == 306

                for action in ("download", "pause", "resume", "repair"):
                    response = await client.post(f"/api/v1/system/tree-sitter/{action}")
                    assert response.status_code == 200
                assert manager.actions == ["download", "pause", "resume", "repair"]
            finally:
                app.dependency_overrides.pop(get_current_user, None)
