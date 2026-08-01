"""翻译端点：离线验证 schema 校验、LLM 调用与未配置分支（mock complete，不发网络）。"""

import httpx
import pytest

from sag_api.generation.llm import LLMClient


async def _register(c):
    import uuid

    email = f"tr-{uuid.uuid4().hex[:10]}@t.com"
    r = await c.post("/api/v1/auth/register", json={"email": email, "password": "password123"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.asyncio
async def test_translate_endpoint_validates_and_calls_llm(monkeypatch: pytest.MonkeyPatch):
    from sag_api.core.config import settings
    from sag_api.main import app

    transport = httpx.ASGITransport(app=app)
    snapshot_llm_configured = settings.llm_api_key
    try:
        # 场景 A：未配置 LLM → 明确错误
        monkeypatch.setattr(settings, "llm_api_key", None)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                A = await _register(c)
                r = await c.post("/api/v1/translate", headers=A, json={"text": "hello", "target_lang": "zh"})
                assert r.status_code == 400, r.text
                assert "尚未配置 LLM" in r.json()["error"]["message"]

                # 空文本 / 超长 → 422
                assert (await c.post("/api/v1/translate", headers=A, json={"text": "   "})).status_code == 422
                assert (
                    await c.post("/api/v1/translate", headers=A, json={"text": "x" * 5001})
                ).status_code == 422
                assert (
                    await c.post("/api/v1/translate", headers=A, json={"text": "hi", "target_lang": "fr"})
                ).status_code == 422

        # 场景 B：配置 LLM 并 mock complete
        monkeypatch.setattr(settings, "llm_api_key", "sk-fake")
        observed: dict = {}

        async def fake_complete(client: LLMClient, messages):
            observed["system"] = messages[0]["content"]
            observed["text"] = messages[1]["content"]
            return "你好，世界"

        monkeypatch.setattr(LLMClient, "complete", fake_complete)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                A = await _register(c)
                r = await c.post(
                    "/api/v1/translate",
                    headers=A,
                    json={"text": "hello world", "target_lang": "zh"},
                )
                assert r.status_code == 200, r.text
                assert r.json()["translated"] == "你好，世界"
                assert "中文" in observed["system"]
                assert observed["text"] == "hello world"

                # 英文目标
                r2 = await c.post(
                    "/api/v1/translate",
                    headers=A,
                    json={"text": "你好", "target_lang": "en"},
                )
                assert r2.status_code == 200
                assert "English" in observed["system"]

                # 模型返回带引号 → 剥掉
                async def fake_quoted(client, messages):
                    return '"quoted result"'

                monkeypatch.setattr(LLMClient, "complete", fake_quoted)
                r3 = await c.post(
                    "/api/v1/translate",
                    headers=A,
                    json={"text": "abc", "target_lang": "en"},
                )
                assert r3.json()["translated"] == "quoted result"

                # 模型返回空 → 明确错误
                async def fake_empty(client, messages):
                    return ""

                monkeypatch.setattr(LLMClient, "complete", fake_empty)
                r4 = await c.post(
                    "/api/v1/translate",
                    headers=A,
                    json={"text": "abc", "target_lang": "en"},
                )
                assert r4.status_code == 502
    finally:
        settings.llm_api_key = snapshot_llm_configured
