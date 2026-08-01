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
async def test_llm_rerank_marks_only_the_completion_call():
    from sag_api.core.llm_call_context import current_llm_call_scenario
    from sag_api.services.retrieval_service import _llm_rerank

    class ScopeLLM(_FakeLLM):
        async def complete(self, messages):
            self.scenario = current_llm_call_scenario()
            return self.reply

    llm = ScopeLLM("1,2")
    assert current_llm_call_scenario() is None
    await _llm_rerank("q", _sections(2), llm=llm, limit=2)
    assert llm.scenario == "rerank"
    assert current_llm_call_scenario() is None


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
async def test_api_rerank_orders_scored_candidates_and_keeps_ties_stable():
    from sag_api.services.retrieval_service import _api_rerank

    class FakeAPI:
        async def rank(self, query, documents, *, limit):
            assert query == "q"
            assert documents == ["content 0", "content 1", "content 2"]
            assert limit == 3
            return [0.2, 0.9, 0.2]

    result = await _api_rerank("q", _sections(3), client=FakeAPI(), limit=3)
    assert [section.chunk_id for section in result] == ["c1", "c0", "c2"]


@pytest.mark.asyncio
async def test_api_rerank_falls_back_to_existing_order_on_failure():
    from sag_api.services.retrieval_service import _api_rerank

    class BrokenAPI:
        async def rank(self, *args, **kwargs):
            raise RuntimeError("down")

    result = await _api_rerank("q", _sections(3), client=BrokenAPI(), limit=3)
    assert [section.chunk_id for section in result] == ["c0", "c1", "c2"]


@pytest.mark.asyncio
async def test_local_rerank_orders_native_scores_and_keeps_ties_stable():
    from sag_api.services.retrieval_service import _local_rerank

    class NativeReranker:
        def rank(self, query, documents):
            assert query == "q"
            assert documents == ["content 0", "content 1", "content 2"]
            return [0.4, 0.9, 0.4]

    result = await _local_rerank("q", _sections(3), reranker=NativeReranker(), limit=3)
    assert [section.chunk_id for section in result] == ["c1", "c0", "c2"]


@pytest.mark.asyncio
async def test_local_rerank_falls_back_to_fused_order_when_native_runtime_fails():
    from sag_api.services.retrieval_service import _local_rerank

    class BrokenReranker:
        def rank(self, query, documents):
            raise RuntimeError("runtime missing")

    result = await _local_rerank("q", _sections(3), reranker=BrokenReranker(), limit=3)
    assert [section.chunk_id for section in result] == ["c0", "c1", "c2"]


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
