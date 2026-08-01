"""SAG-OPT-502：文档状态活动快照端点（旧状态由前端对比推导，本端点只保证字段与排序）。"""

import httpx
import pytest

from sag_api.core.config import settings


async def _register(c):
    import uuid

    email = f"activity-{uuid.uuid4().hex[:10]}@t.com"
    r = await c.post("/api/v1/auth/register", json={"email": email, "password": "password123"})
    assert r.status_code == 201, r.text
    return email, {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.asyncio
async def test_source_activity_snapshot_fields_and_order():
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import delete, select

    from sag_api.core.db import SessionLocal
    from sag_api.db.base import new_id
    from sag_api.db.models import Document, Source, User
    from sag_api.enums import DocumentStatus
    from sag_api.main import app

    transport = httpx.ASGITransport(app=app)
    snapshot_timezone = settings.timezone
    try:
        async with app.router.lifespan_context(app):
            async with SessionLocal() as s:
                user = (await s.scalars(select(User).where(User.email == "activity@t.com"))).first()
                if user is None:
                    user = User(email="activity@t.com", password_hash="x", name="activity")
                    s.add(user)
                    await s.commit()
                    await s.refresh(user)
                source = Source(
                    name="activity-src",
                    connector_kind="file_upload",
                    sag_source_config_id=f"src_act_{new_id()[:12]}",
                )
                s.add(source)
                await s.commit()
                await s.refresh(source)
                now = datetime.now(UTC)
                for index, (status, progress, error) in enumerate(
                    [
                        (DocumentStatus.READY, 100, None),
                        (DocumentStatus.EXTRACTING, 40, None),
                        (DocumentStatus.FAILED, 20, "boom"),
                    ]
                ):
                    s.add(
                        Document(
                            id=new_id(),
                            source_id=source.id,
                            filename=f"f{index}.pdf",
                            storage_path=f"/tmp/f{index}.pdf",
                            status=status,
                            progress=progress,
                            error=error,
                            updated_at=now - timedelta(minutes=index),
                            created_at=now - timedelta(minutes=index),
                        )
                    )
                await s.commit()
                source_id = source.id

            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                _email, A = await _register(c)
                r = await c.get(f"/api/v1/sources/{source_id}/activity?limit=5", headers=A)
                assert r.status_code == 200, r.text
                events = r.json()["events"]
                assert len(events) == 3
                # 按 updated_at 降序
                assert [e["filename"] for e in events] == ["f0.pdf", "f1.pdf", "f2.pdf"]
                first = events[0]
                assert first["document_id"] and first["status"] == "ready"
                assert first["progress"] == 100 and first["error"] is None
                assert events[2]["status"] == "failed" and events[2]["error"] == "boom"
                assert events[2]["progress"] == 20
                assert all(e["updated_at"] for e in events)

                # 越界 limit → 422
                assert (
                    await c.get(f"/api/v1/sources/{source_id}/activity?limit=0", headers=A)
                ).status_code == 422

                # 未知信源 → 404
                assert (
                    await c.get("/api/v1/sources/does-not-exist/activity", headers=A)
                ).status_code == 404
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(Document).where(Document.source_id.in_(select(Source.id).where(Source.name == "activity-src"))))
            await s.execute(delete(Source).where(Source.name == "activity-src"))
            await s.execute(delete(User).where(User.email.like("activity-%@t.com")))
            await s.commit()
        settings.timezone = snapshot_timezone
