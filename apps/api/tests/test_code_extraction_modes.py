from __future__ import annotations

from types import SimpleNamespace

import pytest

from sag_api.sag.incremental_processor import IncrementalDocumentProcessor


def _processor(mode: str = "comments") -> IncrementalDocumentProcessor:
    return IncrementalDocumentProcessor(
        engine=SimpleNamespace(_extractor=SimpleNamespace(prompt_manager=object(), model_config=object())),
        source_config_id="src",
        max_concurrency=1,
        code_llm_extraction_mode=mode,
    )


def _chunk(*, chunk_id: str, chunk_type: str, content: str = "code body", extraction: str | None = None):
    extra = {
        "chunk_type": chunk_type,
        "code_language": "python",
        "content_sha256": "a" * 64,
        "symbol_id": f"sym-{chunk_id}",
    }
    if extraction is not None:
        extra["llm_extraction_text"] = extraction
    return SimpleNamespace(id=chunk_id, extra_data=extra, content=content, heading=chunk_id)


def test_code_extraction_plan_modes():
    off = _processor("off")
    comments = _processor("comments")
    all_mode = _processor("all")

    parent = _chunk(chunk_id="p", chunk_type="code_parent", extraction="doc for class")
    child_with = _chunk(chunk_id="c1", chunk_type="code_child", extraction="doc for method")
    child_without = _chunk(chunk_id="c2", chunk_type="code_child")
    normal = SimpleNamespace(id="n", extra_data={"chunk_type": "child"}, content="plain")

    assert off._code_extraction_plan(parent) == (True, None)
    assert off._code_extraction_plan(child_with) == (True, None)

    assert comments._code_extraction_plan(parent) == (False, "doc for class")
    assert comments._code_extraction_plan(child_with) == (False, "doc for method")
    assert comments._code_extraction_plan(child_without) == (True, None)

    assert all_mode._code_extraction_plan(parent) == (True, None)
    assert all_mode._code_extraction_plan(child_with) == (False, None)
    assert all_mode._code_extraction_plan(child_without) == (False, None)

    # Non-code keeps legacy path
    assert off._code_extraction_plan(normal) == (False, None)
    assert comments._code_extraction_plan(normal) == (False, None)
    assert all_mode._code_extraction_plan(normal) == (False, None)


@pytest.mark.asyncio
async def test_extract_chunk_off_marks_eventless_without_llm(monkeypatch):
    processor = _processor("off")
    calls = {"extract": 0, "llm": 0}

    async def fake_load(chunk_id: str):
        return _chunk(chunk_id=chunk_id, chunk_type="code_child", extraction="note")

    monkeypatch.setattr(processor, "_load_source_chunk", fake_load)

    class BoomExtractor:
        def __init__(self, *args, **kwargs):
            calls["extract"] += 1

        async def _get_llm_client(self):
            calls["llm"] += 1
            raise AssertionError("LLM should not be created in off mode")

        async def extract(self, config):
            raise AssertionError("extract should not run in off mode")

    monkeypatch.setattr("sag_api.sag.incremental_processor.EventExtractor", BoomExtractor)
    event_ids, tokens = await processor._extract_chunk("c1")
    assert event_ids == []
    assert tokens == 0
    assert calls == {"extract": 0, "llm": 0}


@pytest.mark.asyncio
async def test_extract_chunk_comments_rewrites_section_content(monkeypatch):
    processor = _processor("comments")
    seen = {}

    async def fake_load(chunk_id: str):
        return _chunk(chunk_id=chunk_id, chunk_type="code_child", content="SECRET SOURCE", extraction="only comments")

    monkeypatch.setattr(processor, "_load_source_chunk", fake_load)

    class FakeExtractor:
        def __init__(self, *args, **kwargs):
            self.prompt_manager = object()
            self.model_config = object()
            self._load_chunk_content = self._original_load

        async def _get_llm_client(self):
            class Client:
                async def chat(self, *args, **kwargs):
                    return SimpleNamespace(content="{}", usage=SimpleNamespace(total_tokens=3))

            return Client()

        async def _original_load(self, chunk, config):
            section = SimpleNamespace(content=chunk.content, heading=chunk.heading, type="CODE")
            return [section], {"title": "t"}

        async def extract(self, config):
            # Simulate extract_from_chunk path using the (possibly patched) loader.
            chunk = await processor._load_source_chunk(config.chunk_ids[0])
            sections, _meta = await self._load_chunk_content(chunk, config)
            seen["contents"] = [s.content for s in sections]
            return [SimpleNamespace(id="e1")]

        async def extract_from_chunk(self, chunk, config):
            return []

    monkeypatch.setattr("sag_api.sag.incremental_processor.EventExtractor", FakeExtractor)
    monkeypatch.setattr("sag_api.sag.incremental_processor._llm_chat_owner", lambda client: SimpleNamespace(chat=client.chat))
    monkeypatch.setattr("sag_api.sag.incremental_processor._response_token_usage", lambda response: 3)
    monkeypatch.setattr("sag_api.sag.incremental_processor._normalize_extraction_response", lambda *a, **k: 0)
    monkeypatch.setattr("sag_api.sag.incremental_processor._entity_types_from_messages", lambda messages: [])
    monkeypatch.setattr("sag_api.sag.incremental_processor.llm_call_scope", lambda *_a, **_k: __import__("contextlib").nullcontext())

    event_ids, tokens = await processor._extract_chunk("c1")
    assert event_ids == ["e1"]
    assert tokens >= 0
    assert seen["contents"] == ["only comments"]


@pytest.mark.asyncio
async def test_extract_chunk_all_skips_parent_keeps_child_source(monkeypatch):
    processor = _processor("all")

    async def fake_load(chunk_id: str):
        if chunk_id == "parent":
            return _chunk(chunk_id=chunk_id, chunk_type="code_parent", content="PARENT")
        return _chunk(chunk_id=chunk_id, chunk_type="code_child", content="CHILD SOURCE")

    monkeypatch.setattr(processor, "_load_source_chunk", fake_load)

    class FakeExtractor:
        def __init__(self, *args, **kwargs):
            self.seen = None
            self._load_chunk_content = self._original_load

        async def _get_llm_client(self):
            class Client:
                async def chat(self, *args, **kwargs):
                    return SimpleNamespace(content="{}", usage=SimpleNamespace(total_tokens=2))

            return Client()

        async def _original_load(self, chunk, config):
            return [SimpleNamespace(content=chunk.content, heading=chunk.heading)], {}

        async def extract(self, config):
            chunk = await processor._load_source_chunk(config.chunk_ids[0])
            sections, _ = await self._load_chunk_content(chunk, config)
            self.seen = [s.content for s in sections]
            return [SimpleNamespace(id="e2")]

        async def extract_from_chunk(self, chunk, config):
            return []

    created = {}

    def factory(*args, **kwargs):
        created["ext"] = FakeExtractor()
        return created["ext"]

    monkeypatch.setattr("sag_api.sag.incremental_processor.EventExtractor", factory)
    monkeypatch.setattr("sag_api.sag.incremental_processor._llm_chat_owner", lambda client: SimpleNamespace(chat=client.chat))
    monkeypatch.setattr("sag_api.sag.incremental_processor._response_token_usage", lambda response: 2)
    monkeypatch.setattr("sag_api.sag.incremental_processor._normalize_extraction_response", lambda *a, **k: 0)
    monkeypatch.setattr("sag_api.sag.incremental_processor._entity_types_from_messages", lambda messages: [])
    monkeypatch.setattr("sag_api.sag.incremental_processor.llm_call_scope", lambda *_a, **_k: __import__("contextlib").nullcontext())

    parent_events, parent_tokens = await processor._extract_chunk("parent")
    assert parent_events == []
    assert parent_tokens == 0
    assert "ext" not in created

    child_events, child_tokens = await processor._extract_chunk("child")
    assert child_events == ["e2"]
    assert child_tokens >= 0
    assert created["ext"].seen == ["CHILD SOURCE"]


@pytest.mark.asyncio
async def test_extract_remaining_marks_skipped_code_chunks_processed(monkeypatch):
    from sag_api.sag.dto import ProcessCheckpoint

    processor = _processor("off")
    checkpoint = ProcessCheckpoint(chunk_ids=["a", "b"], processed_chunk_ids=[], eventless_chunk_ids=[])

    async def fake_extract(chunk_id: str):
        return [], 0

    monkeypatch.setattr(processor, "_extract_chunk", fake_extract)

    async def on_checkpoint(value):
        checkpoint.processed_chunk_ids = list(value.processed_chunk_ids)
        checkpoint.eventless_chunk_ids = list(value.eventless_chunk_ids)

    async def never_pause():
        return False

    await processor._extract_remaining(
        ["a", "b"],
        current=checkpoint,
        on_checkpoint=on_checkpoint,
        should_pause=never_pause,
    )
    assert checkpoint.processed_chunk_ids == ["a", "b"]
    assert checkpoint.eventless_chunk_ids == ["a", "b"]
