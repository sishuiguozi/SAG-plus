"""删除信源需要账户密码：错密码 403、正确密码删除成功、无密码 422。"""

import httpx
import pytest


async def _register(c):
    import uuid

    email = f"del-{uuid.uuid4().hex[:10]}@t.com"
    r = await c.post("/api/v1/auth/register", json={"email": email, "password": "password123"})
    assert r.status_code == 201, r.text
    return email, {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.asyncio
async def test_delete_source_requires_password():
    from sqlalchemy import delete, select

    from sag_api.core.db import SessionLocal
    from sag_api.db.models import Source, User
    from sag_api.main import app

    transport = httpx.ASGITransport(app=app)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                email, A = await _register(c)
                # 建一个测试信源
                r = await c.post("/api/v1/sources", headers=A, json={"name": "del-me", "connector_kind": "file_upload"})
                assert r.status_code == 201, r.text
                source_id = r.json()["id"]

                # 缺密码 → 422
                assert (await c.request("DELETE", f"/api/v1/sources/{source_id}", headers=A)).status_code == 422

                # 错误密码 → 403
                r = await c.request(
                    "DELETE",
                    f"/api/v1/sources/{source_id}",
                    headers=A,
                    json={"password": "wrong-password"},
                )
                assert r.status_code == 403, r.text
                assert "密码不正确" in r.json()["error"]["message"]

                # 信源仍存在
                assert (await c.get(f"/api/v1/sources/{source_id}", headers=A)).status_code == 200

                # 正确密码 → 200 且删除
                r = await c.request(
                    "DELETE",
                    f"/api/v1/sources/{source_id}",
                    headers=A,
                    json={"password": "password123"},
                )
                assert r.status_code == 200, r.text
                assert (await c.get(f"/api/v1/sources/{source_id}", headers=A)).status_code == 404
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(Source).where(Source.name == "del-me"))
            await s.execute(delete(User).where(User.email.like("del-%@t.com")))
            await s.commit()
