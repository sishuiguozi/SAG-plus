from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from sag_api.api.v1.sources import router as sources_router
from sag_api.core.deps import get_current_user, get_session
from sag_api.core.errors import ApiError
from sag_api.schemas.source import SourceCodeConfig
from sag_api.services import source_service


class FakeSession:
    def __init__(self, source):
        self.source = source
        self.committed = 0

    async def get(self, model, ident):
        return self.source if getattr(self.source, "id", None) == ident else None

    async def commit(self):
        self.committed += 1

    async def refresh(self, obj):
        return None


@pytest.mark.asyncio
async def test_source_code_config_defaults_updates_and_hides_other_config(monkeypatch):
    source = SimpleNamespace(
        id="src-1",
        config={"api_key": "must-not-leak", "connector": {"token": "secret"}},
    )

    async def fake_get_source(session, source_id):
        assert source_id == "src-1"
        return source

    monkeypatch.setattr(source_service, "get_source", fake_get_source)

    app = FastAPI()
    app.include_router(sources_router, prefix="/api/v1")

    @app.exception_handler(ApiError)
    async def _api_error_handler(_request, exc: ApiError):
        return JSONResponse(status_code=exc.status_code, content={"error": {"message": str(exc)}})

    async def override_session():
        yield FakeSession(source)

    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="test-user")
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        initial = await client.get("/api/v1/sources/src-1/code-config")
        assert initial.status_code == 200
        assert initial.json() == {"llm_extraction_mode": "comments"}

        updated = await client.patch(
            "/api/v1/sources/src-1/code-config",
            json={"llm_extraction_mode": "off"},
        )
        assert updated.status_code == 200
        assert updated.json() == {"llm_extraction_mode": "off"}
        assert "api_key" not in updated.text
        assert "secret" not in updated.text
        assert source.config["api_key"] == "must-not-leak"
        assert source.config["connector"] == {"token": "secret"}
        assert source.config["code_ingest"] == {"llm_extraction_mode": "off"}

        invalid = await client.patch(
            "/api/v1/sources/src-1/code-config",
            json={"llm_extraction_mode": "sometimes"},
        )
        assert invalid.status_code == 422

    app.dependency_overrides.clear()


def test_read_source_code_config_defaults():
    cfg = source_service._read_source_code_config(SimpleNamespace(config=None))
    assert cfg == SourceCodeConfig(llm_extraction_mode="comments")
