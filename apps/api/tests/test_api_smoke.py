"""HTTP 层冒烟：跑真实 ASGI 应用（含 lifespan / 后台队列），全程离线。"""

import httpx
import pytest


@pytest.mark.asyncio
async def test_end_to_end_offline():
    from sag_api.main import app

    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            # 系统
            assert (await c.get("/api/v1/system/health")).json()["status"] == "ok"
            caps = (await c.get("/api/v1/system/capabilities")).json()
            assert caps["llm_configured"] is False

            # 认证：注册单账号
            r = await c.post(
                "/api/v1/auth/register",
                json={"email": "a@b.com", "password": "password123", "name": "Ada"},
            )
            assert r.status_code == 201
            tok = r.json()["access_token"]
            H = {"Authorization": f"Bearer {tok}"}

            assert (await c.get("/api/v1/auth/me", headers=H)).json()["email"] == "a@b.com"
            assert (await c.get("/api/v1/auth/me")).status_code == 401
            local_login = await c.post("/api/v1/auth/login", json={"name": "Ada"})
            assert local_login.status_code == 200
            assert local_login.json()["user"]["id"] == r.json()["user"]["id"]
            login = await c.post(
                "/api/v1/auth/login",
                json={"name": "Ada", "email": "a@b.com", "password": "password123"},
            )
            assert login.status_code == 200
            assert login.json()["user"]["id"] == r.json()["user"]["id"]
            dup = await c.post(
                "/api/v1/auth/register", json={"email": "a@b.com", "password": "password123"}
            )
            assert dup.status_code == 409

            # 连接器 + 信源
            conns = (await c.get("/api/v1/sources/connectors", headers=H)).json()
            assert any(x["kind"] == "file_upload" for x in conns)

            r = await c.post("/api/v1/sources", headers=H, json={"name": "手册"})
            assert r.status_code == 201
            sid = r.json()["id"]
            assert r.json()["source_type"] == "document"
            # 共享测试库 → 用存在性/按 id 定位而非精确计数
            def _find(sources):
                return next(s for s in sources if s["id"] == sid)

            assert _find((await c.get("/api/v1/sources", headers=H)).json())["id"] == sid

            # 上传（不等待后台完成，避免 401 重试拖慢测试）
            up = await c.post(
                f"/api/v1/sources/{sid}/documents",
                headers=H,
                files={"file": ("a.md", b"# T\n\nhello world\n", "text/markdown")},
            )
            assert up.status_code == 201 and up.json()["status"] == "pending"
            source_after_upload = _find((await c.get("/api/v1/sources", headers=H)).json())
            assert source_after_upload["document_count"] == 1
            assert (
                source_after_upload["ready_document_count"]
                + source_after_upload["pending_document_count"]
                - source_after_upload["paused_document_count"]
                + source_after_upload["paused_document_count"]
                + source_after_upload["failed_document_count"]
            ) == 1

            # 统一写入接口：持续推送一批消息 → 归一为文档进入管线
            ing = await c.post(
                f"/api/v1/sources/{sid}/documents/ingest",
                headers=H,
                json={"messages": [{"author": "张三", "text": "明天评审几点？", "ts": "2026-07-07T09:00Z"}]},
            )
            assert ing.status_code == 201 and ing.json()["status"] == "pending"
            source_after_ingest = _find((await c.get("/api/v1/sources", headers=H)).json())
            assert source_after_ingest["document_count"] == 2
            assert (
                source_after_ingest["ready_document_count"]
                + source_after_ingest["pending_document_count"]
                - source_after_ingest["paused_document_count"]
                + source_after_ingest["paused_document_count"]
                + source_after_ingest["failed_document_count"]
            ) == 2
            ingest_stats = (await c.get("/api/v1/sources/ingest-stats", headers=H)).json()
            assert ingest_stats["total_files"] >= 2
            assert (
                ingest_stats["indexed_files"]
                + ingest_stats["pending_files"]
                + ingest_stats["failed_files"]
                >= 2
            )
            assert ingest_stats["docs_per_minute"] >= 0

            # 全局搜索：离线（无 embedding）单源失败被吞，返回 200 + 空结果
            gs = await c.post("/api/v1/search", headers=H, json={"query": "hello"})
            assert gs.status_code == 200
            body = gs.json()
            assert body["query"] == "hello" and isinstance(body["sections"], list)
            # 收窄到指定信源
            gs2 = await c.post(
                "/api/v1/search", headers=H, json={"query": "hello", "source_ids": [sid]}
            )
            assert gs2.status_code == 200
