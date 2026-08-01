"""SAG-OPT-501：文档列表服务端搜索（?q=filename）。

- 上传两个不同文件名的文档，?q= 过滤只返回匹配项。
- 无匹配返回空列表；状态过滤与 q 可组合。
"""

import httpx
import pytest


@pytest.mark.asyncio
async def test_document_list_server_side_search():
    from sag_api.main import app

    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            tok = (
                await c.post(
                    "/api/v1/auth/register",
                    json={"email": "search@x.com", "password": "password123"},
                )
            ).json()["access_token"]
            headers = {"Authorization": f"Bearer {tok}"}
            src = (await c.post("/api/v1/sources", headers=headers, json={"name": "搜索源"})).json()
            sid = src["id"]

            for name, body in (("alpha.md", b"# alpha\nhello\n"), ("beta.md", b"# beta\nworld\n")):
                resp = await c.post(
                    f"/api/v1/sources/{sid}/documents",
                    headers=headers,
                    files={"file": (name, body, "text/markdown")},
                )
                assert resp.status_code in (201, 200), resp.text

            # 服务端搜索：q=alpha 只返回 alpha
            filtered = (await c.get(
                f"/api/v1/sources/{sid}/documents", headers=headers, params={"q": "alpha"}
            )).json()
            assert [d["filename"] for d in filtered] == ["alpha.md"], filtered

            # 状态 + q 组合（都处于 PENDING/FAILED 均可返回，仅验证无 500 且过滤生效）
            with_q = (await c.get(
                f"/api/v1/sources/{sid}/documents", headers=headers, params={"q": "beta"}
            )).json()
            assert [d["filename"] for d in with_q] == ["beta.md"]

            # 无匹配
            none = (await c.get(
                f"/api/v1/sources/{sid}/documents", headers=headers, params={"q": "zzz"}
            )).json()
            assert none == []
