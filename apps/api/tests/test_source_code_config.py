from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest


@pytest.mark.asyncio
async def test_source_code_config_defaults_updates_and_hides_other_config():
    from sag_api.core.db import SessionLocal
    from sag_api.core.deps import get_current_user
    from sag_api.db.models import Source
    from sag_api.enums import ConnectorKind, SourceStatus, SourceType
    from sag_api.main import app

    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="test-user")
    transport = httpx.ASGITransport(app=app)
    try:
        async with app.router.lifespan_context(app):
            source = Source(
                name="代码库",
                description="",
                source_type=SourceType.DOCUMENT,
                connector_kind=ConnectorKind.FILE_UPLOAD,
                sag_source_config_id="src_code_config_test",
                config={"api_key": "must-not-leak", "connector": {"token": "secret"}},
                status=SourceStatus.ACTIVE,
            )
            async with SessionLocal() as session:
                session.add(source)
                await session.commit()
                await session.refresh(source)
                source_id = source.id

            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                initial = await client.get(f"/api/v1/sources/{source_id}/code-config")
                assert initial.status_code == 200
                assert initial.json() == {"llm_extraction_mode": "comments"}

                updated = await client.patch(
                    f"/api/v1/sources/{source_id}/code-config",
                    json={"llm_extraction_mode": "off"},
                )
                assert updated.status_code == 200
                assert updated.json() == {"llm_extraction_mode": "off"}
                assert "api_key" not in updated.text
                assert "secret" not in updated.text

                invalid = await client.patch(
                    f"/api/v1/sources/{source_id}/code-config",
                    json={"llm_extraction_mode": "sometimes"},
                )
                assert invalid.status_code == 422

            async with SessionLocal() as session:
                persisted = await session.get(Source, source_id)
                assert persisted is not None
                assert persisted.config["api_key"] == "must-not-leak"
                assert persisted.config["connector"] == {"token": "secret"}
                assert persisted.config["code_ingest"] == {"llm_extraction_mode": "off"}
    finally:
        app.dependency_overrides.pop(get_current_user, None)
