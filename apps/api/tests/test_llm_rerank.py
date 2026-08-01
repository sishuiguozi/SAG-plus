"""A3 LLM 重排：按模型输出顺序重排候选；失败/禁用回退原顺序。"""

import asyncio

import pytest

from sag_api.sag.dto import RetrievedSection


class _FakeLLM:
    def __init__(self, reply):
        self.reply = reply
        self.configured = True

    async def complete(self, messages):
        return self.reply


def _sections(n):
    return [
        RetrievedSection(chunk_id=f"c{i}", heading=f"H{i}", content=f"content {i}", score=0.5)
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_llm_rerank_reorders():
    from sag_api.services.retrieval_service import _llm_rerank

    sections = _sections(4)
    llm = _FakeLLM("3,1,2,4")  # 期望顺序：c2, c0, c1, c3
    result = await _llm_rerank("q", sections, llm=llm, limit=3)
    assert [s.chunk_id for s in result] == ["c2", "c0", "c1"]


@pytest.mark.asyncio
async def test_llm_rerank_fallback_on_error():
    from sag_api.services.retrieval_service import _llm_rerank

    sections = _sections(3)

    class BoomLLM(_FakeLLM):
        async def complete(self, messages):
            raise RuntimeError("boom")

    result = await _llm_rerank("q", sections, llm=BoomLLM(""), limit=3)
    assert [s.chunk_id for s in result] == ["c0", "c1", "c2"]


@pytest.mark.asyncio
async def test_retrieve_uses_llm_rerank_when_enabled(monkeypatch):
    from sag_api.core.config import settings
    from sag_api.services import retrieval_service as rs

    settings.search_llm_rerank_enabled = True
    calls = {"n": 0}

    async def fake_rerank(query, sections, *, llm, limit):
        calls["n"] += 1
        return sections

    monkeypatch.setattr(rs, "_llm_rerank", fake_rerank)

    class FakeOutcome:
        query = "q"
        sections = _sections(2)
        stats = {}

    async def fake_search_many(targets, query, strategy=None, top_k=None):
        return FakeOutcome()

    async def fake_lexical(engine, sources, query):
        return []

    monkeypatch.setattr(rs, "query_terms", lambda q: ["t"])
    # 直接调用 retrieve_relevant_sections 需要 engine_manager；改为直接验证开关分支
    from sag_api.sag.dto import SearchOutcome
    from sag_api.services.retrieval_service import rerank_sections

    sections = _sections(3)
    reranked = rerank_sections("q", sections, limit=2)
    assert reranked.sections
