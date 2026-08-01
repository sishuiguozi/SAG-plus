"""检索结果 TTL 缓存：相同查询命中缓存、TTL 过期失效、深拷贝隔离。"""


import pytest

from sag_api.sag.dto import RetrievedSection, SearchOutcome


async def _make_manager(ttl: int):
    from sag_api.core.config import Settings
    from sag_api.sag.engine_manager import EngineManager

    settings = Settings(
        search_cache_ttl_seconds=ttl,
        engine_cache_size=2,
        search_source_candidate_limit=8,
        search_source_concurrency=2,
        search_top_k=8,
    )
    manager = EngineManager(settings)
    return manager


async def _stub_outcome(query: str) -> SearchOutcome:
    return SearchOutcome(
        query=query,
        sections=[
            RetrievedSection(
                chunk_id="c1",
                heading="h",
                content="内容",
                score=0.9,
                source_config_id="src-1",
            )
        ],
        stats={"candidates": 1},
    )


@pytest.mark.asyncio
async def test_search_many_second_call_hits_cache(monkeypatch):
    manager = await _make_manager(ttl=60)
    calls = {"n": 0}

    async def fake_impl(targets, query, *, strategy=None, top_k=None):
        calls["n"] += 1
        return await _stub_outcome(query)

    monkeypatch.setattr(manager, "_search_many_impl", fake_impl)

    targets = [("src-1", None), ("src-2", None)]
    first = await manager.search_many(targets, "测试查询")
    second = await manager.search_many(targets, "测试查询")
    assert first.sections[0].chunk_id == "c1"
    assert second.sections[0].chunk_id == "c1"
    assert calls["n"] == 1, "第二次调用应命中缓存（不重复执行）"

    # 不同查询不命中
    await manager.search_many(targets, "另一个查询")
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_cache_disabled_when_ttl_zero(monkeypatch):
    manager = await _make_manager(ttl=0)
    calls = {"n": 0}

    async def fake_impl(targets, query, *, strategy=None, top_k=None):
        calls["n"] += 1
        return await _stub_outcome(query)

    monkeypatch.setattr(manager, "_search_many_impl", fake_impl)
    targets = [("src-1", None)]
    await manager.search_many(targets, "q")
    await manager.search_many(targets, "q")
    assert calls["n"] == 2, "ttl=0 时应禁用缓存"


@pytest.mark.asyncio
async def test_search_single_source_cached(monkeypatch):
    manager = await _make_manager(ttl=60)
    calls = {"n": 0}

    async def fake_raw(source_config_id, query, **kwargs):
        calls["n"] += 1
        return await _stub_outcome(query)

    monkeypatch.setattr(manager, "_search_raw", fake_raw)
    monkeypatch.setattr(manager, "_effective_search_strategy", lambda s: s or "vector")

    await manager.search("src-1", "q")
    await manager.search("src-1", "q")
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_cached_outcome_is_deep_copied(monkeypatch):
    """调用方修改返回的 section 不得污染缓存。"""
    manager = await _make_manager(ttl=60)

    async def fake_impl(targets, query, *, strategy=None, top_k=None):
        return await _stub_outcome(query)

    monkeypatch.setattr(manager, "_search_many_impl", fake_impl)
    targets = [("src-1", None)]
    first = await manager.search_many(targets, "q")
    first.sections[0].source_config_id = "tampered"
    second = await manager.search_many(targets, "q")
    assert second.sections[0].source_config_id == "src-1", "缓存对象应被深拷贝隔离"
