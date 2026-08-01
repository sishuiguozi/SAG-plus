"""BM25 FTS 稀疏召回：优先 FTS、失败回退 grep 通道。"""

import asyncio

import pytest

from sag_api.sag.dto import RetrievedSection


class _FakeEngine:
    def __init__(self, grep_rows=None):
        self.grep_rows = grep_rows or []
        self.grep_calls = 0

    async def grep_chunks(self, source_config_id, term, *, source=None, limit=2):
        self.grep_calls += 1
        return self.grep_rows


async def _lexical(engine, sources, query):
    from sag_api.services.retrieval_service import _lexical_sections

    return await _lexical_sections(engine, sources, query)


class _Source:
    def __init__(self, sid, scid):
        self.id = sid
        self.sag_source_config_id = scid


@pytest.mark.asyncio
async def test_fts_channel_used_when_available(monkeypatch):
    from sag_api.core.config import settings
    import sag_api.sag.lancedb_fts as fts_mod

    engine = _FakeEngine()
    settings.lancedb_fts_enabled = True
    settings.search_top_k = 8

    fake_rows = [
        {
            "chunk_id": "c-1",
            "heading": "AFSIM Config",
            "content": "terrain configuration",
            "source_config_id": "src-1",
            "_score": 10.0,
        }
    ]
    monkeypatch.setattr(fts_mod, "fts_search", lambda *a, **k: fake_rows)

    sections = await _lexical(engine, [_Source("s", "src-1")], "AFSIM terrain")
    assert sections
    assert sections[0].chunk_id == "c-1"
    assert sections[0].heading == "AFSIM Config"
    assert engine.grep_calls == 0, "FTS 可用时不应走 grep"


@pytest.mark.asyncio
async def test_fts_channel_runs_in_worker_thread(monkeypatch):
    from sag_api.core.config import settings
    import sag_api.sag.lancedb_fts as fts_mod
    import sag_api.services.retrieval_service as retrieval_mod

    engine = _FakeEngine()
    settings.lancedb_fts_enabled = True
    calls = []

    async def fake_to_thread(func, /, *args, **kwargs):
        calls.append((func, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr(fts_mod, "fts_search", lambda *a, **k: [])
    monkeypatch.setattr(retrieval_mod.asyncio, "to_thread", fake_to_thread)

    await _lexical(engine, [_Source("s", "src-1")], "AFSIM terrain")

    assert calls and calls[0][0] is fts_mod.fts_search


@pytest.mark.asyncio
async def test_fallback_to_grep_when_fts_empty(monkeypatch):
    from sag_api.core.config import settings
    import sag_api.sag.lancedb_fts as fts_mod

    engine = _FakeEngine(grep_rows=[{"chunk_id": "g-1", "heading": "biao ti", "snippet": "content"}])
    settings.lancedb_fts_enabled = True
    monkeypatch.setattr(fts_mod, "fts_search", lambda *a, **k: [])

    sections = await _lexical(engine, [_Source("s", "src-1")], "biao ti")
    assert sections
    assert sections[0].chunk_id == "g-1"
    assert engine.grep_calls >= 1, "FTS 空结果应回退 grep"


@pytest.mark.asyncio
async def test_grep_used_when_fts_disabled(monkeypatch):
    from sag_api.core.config import settings

    engine = _FakeEngine(grep_rows=[{"chunk_id": "g-1", "heading": "", "snippet": "x"}])
    settings.lancedb_fts_enabled = False
    sections = await _lexical(engine, [_Source("s", "src-1")], "query word")
    assert sections
    assert engine.grep_calls >= 1
