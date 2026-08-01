import json

import httpx
import pytest

from sag_api.sag.rerank_api_client import RerankAPIClient


@pytest.mark.asyncio
async def test_rerank_api_posts_qwen_compatible_payload_and_maps_scores():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"results": [
            {"index": 1, "relevance_score": 0.91},
            {"index": 0, "relevance_score": 0.33},
        ]})

    client = RerankAPIClient(
        url="https://dashscope.example/v1/reranks",
        api_key="secret",
        model="qwen3-rerank",
        instruction="rank passages",
        transport=httpx.MockTransport(handler),
    )

    scores = await client.rank("question", ["first", "second"], limit=2)

    assert scores == [0.33, 0.91]
    assert seen["url"] == "https://dashscope.example/v1/reranks"
    assert seen["authorization"] == "Bearer secret"
    assert seen["payload"] == {
        "model": "qwen3-rerank", "query": "question", "documents": ["first", "second"],
        "top_n": 2, "instruct": "rank passages",
    }


@pytest.mark.asyncio
async def test_rerank_api_rejects_invalid_indexes_without_leaking_key():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [
            {"index": 99, "relevance_score": 1.0},
            {"index": 0, "relevance_score": "not-a-score"},
        ]})

    client = RerankAPIClient(
        url="http://127.0.0.1:8000/v1/rerank", api_key="very-secret", model="local",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RuntimeError, match="no valid") as error:
        await client.rank("q", ["one"], limit=1)
    assert "very-secret" not in str(error.value)


@pytest.mark.asyncio
async def test_rerank_api_test_endpoint_uses_unsaved_credentials(monkeypatch):
    from sag_api.main import app

    async def fake_rank(self, query, documents, *, limit):
        assert self.api_key == "draft-secret"
        assert query
        assert len(documents) == 2
        return [0.9, 0.1]

    monkeypatch.setattr(RerankAPIClient, "rank", fake_rank)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            registration = await client.post(
                "/api/v1/auth/register",
                json={"email": "rerank-api@t.com", "password": "password123"},
            )
            headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}
            response = await client.post("/api/v1/system/reranker-api/test", headers=headers, json={
                "url": "https://example.test/reranks", "api_key": "draft-secret", "model": "qwen3-rerank",
            })

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["score_count"] == 2
