"""修改密码：当前密码校验、新密码长度、修改后旧密码失效新密码可登录。"""

import httpx
import pytest


async def _register(c):
    import uuid

    email = f"pwd-{uuid.uuid4().hex[:10]}@t.com"
    r = await c.post("/api/v1/auth/register", json={"email": email, "password": "password123"})
    assert r.status_code == 201, r.text
    return email, r.json()["access_token"]


@pytest.mark.asyncio
async def test_change_password_flow():
    from sqlalchemy import delete

    from sag_api.core.db import SessionLocal
    from sag_api.db.models import User
    from sag_api.main import app

    transport = httpx.ASGITransport(app=app)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                email, token = await _register(c)
                A = {"Authorization": f"Bearer {token}"}

                # 当前密码错误 → 403
                r = await c.put(
                    "/api/v1/auth/password",
                    headers=A,
                    json={"current_password": "wrong-old", "new_password": "newpass123"},
                )
                assert r.status_code == 403, r.text
                assert "当前密码不正确" in r.json()["error"]["message"]

                # 新密码太短 → 422
                r = await c.put(
                    "/api/v1/auth/password",
                    headers=A,
                    json={"current_password": "password123", "new_password": "short"},
                )
                assert r.status_code == 422

                # 修改成功
                r = await c.put(
                    "/api/v1/auth/password",
                    headers=A,
                    json={"current_password": "password123", "new_password": "newpass456"},
                )
                assert r.status_code == 200, r.text
                assert r.json()["email"] == email

                # 旧密码登录失败，新密码登录成功
                old_login = await c.post(
                    "/api/v1/auth/login",
                    json={"name": "x", "email": email, "password": "password123"},
                )
                assert old_login.status_code == 401
                new_login = await c.post(
                    "/api/v1/auth/login",
                    json={"name": "x", "email": email, "password": "newpass456"},
                )
                assert new_login.status_code == 200, new_login.text
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(User).where(User.email.like("pwd-%@t.com")))
            await s.commit()
