from __future__ import annotations

from pathlib import Path

import pytest


def _parsed_document(source: str):
    from sag_api.code_ingest.types import CodeSpan, CodeSymbol, ParsedCodeDocument

    class_start = source.index("class Service")
    method_start = source.index("    def run")
    method_end = len(source)
    method = CodeSymbol(
        identity="source-1:repo/service.py:method:Service.run",
        kind="method",
        name="run",
        qualified_name="Service.run",
        ancestor_path=("repo/service.py", "Service"),
        span=CodeSpan(method_start, method_end, 2, 3),
        signature="def run(self):",
    )
    container = CodeSymbol(
        identity="source-1:repo/service.py:class:Service",
        kind="class",
        name="Service",
        qualified_name="Service",
        ancestor_path=("repo/service.py",),
        span=CodeSpan(class_start, len(source), 1, 3),
        signature="class Service:",
        children=(method,),
    )
    return ParsedCodeDocument(
        source_id="source-1",
        relative_path="repo/service.py",
        content_sha256="f" * 64,
        language="python",
        source=source,
        symbols=(container,),
    )


@pytest.mark.asyncio
async def test_precomputed_parser_returns_code_sections_without_markdown_split(tmp_path: Path):
    from sag_api.code_ingest.loader import PrecomputedCodeParser

    source = "class Service:\n    def run(self):\n        return 1\n"
    path = tmp_path / "service.py"
    path.write_text(source, encoding="utf-8")
    parser = PrecomputedCodeParser(_parsed_document(source), max_child_tokens=100)

    content, section_count = await parser.parse_file_async(path)
    result = parser.get_last_chunking_result()

    assert content == source
    assert section_count == len(result.article_sections) == len(result.source_chunks)
    assert [section.section_type for section in result.article_sections] == ["CODE", "CODE", "CODE"]
    assert [chunk.metadata["chunk_type"] for chunk in result.source_chunks] == [
        "code_parent",
        "code_parent",
        "code_child",
    ]
    assert parser.extract_title(content) == "repo/service.py"


@pytest.mark.asyncio
async def test_code_loader_forces_precomputed_parser_path(monkeypatch, tmp_path: Path):
    from zleap.sag.modules.load.config import DocumentLoadConfig

    from sag_api.code_ingest.loader import CodeDocumentLoader, PrecomputedCodeParser

    monkeypatch.setattr(
        "zleap.sag.modules.load.loader.get_session_factory",
        lambda: object(),
    )
    source = "class Service:\n    def run(self):\n        return 1\n"
    path = tmp_path / "service.py"
    path.write_text(source, encoding="utf-8")
    loader = CodeDocumentLoader(PrecomputedCodeParser(_parsed_document(source)))
    seen = {}

    async def fake_load_file(**kwargs):
        seen.update(kwargs)
        return "loaded"

    monkeypatch.setattr(loader, "load_file", fake_load_file)
    # App-level parent_child mode must not be required by zleap config validation.
    result = await loader.load(
        DocumentLoadConfig(
            path=path,
            source_config_id="source-config",
            max_tokens=777,
            chunk_mode="standard",
        )
    )

    assert result == "loaded"
    assert seen["max_tokens"] is None
    assert seen["chunk_mode"] is None
    assert seen["file_path"] == path


@pytest.mark.asyncio
async def test_incremental_processor_normalizes_unsupported_chunk_mode(monkeypatch, tmp_path: Path):
    from sag_api.parsing.service import PreparedDocument
    from sag_api.sag.dto import ProcessCheckpoint
    from sag_api.sag.incremental_processor import IncrementalDocumentProcessor

    source = "class Service:\n    def run(self):\n        return 1\n"
    path = tmp_path / "service.py"
    path.write_text(source, encoding="utf-8")
    prepared = PreparedDocument(
        path=str(path),
        provider="tree_sitter",
        relative_path="repo/service.py",
        content_sha256="f" * 64,
        code_language="python",
    )
    processor = IncrementalDocumentProcessor(
        engine=object(),
        source_config_id="source-config",
        max_concurrency=1,
        chunk_mode="parent_child",
        prepared_document=prepared,
        code_source_id="source-1",
    )

    seen = {}

    class DummyLoaded:
        source_id = "article-1"
        chunk_ids = ["c1"]

    async def fake_load(config):
        seen["chunk_mode"] = config.chunk_mode
        seen["path"] = str(config.path)
        return DummyLoaded()

    def fake_create_loader(prepared_document, *, source_id, max_child_tokens):
        assert prepared_document is prepared
        assert source_id == "source-1"
        assert max_child_tokens == processor._chunk_max_tokens

        class FakeLoader:
            async def load(self, config):
                return await fake_load(config)

        return FakeLoader()

    monkeypatch.setattr(
        "sag_api.code_ingest.loader.create_code_document_loader",
        fake_create_loader,
    )

    async def on_checkpoint(_checkpoint):
        return None

    async def should_pause():
        return True

    outcome = await processor.process(
        path,
        checkpoint=ProcessCheckpoint(),
        on_checkpoint=on_checkpoint,
        should_pause=should_pause,
    )

    assert seen["chunk_mode"] == "standard"
    assert seen["path"] == str(path)
    assert outcome.source_id == "article-1"
    assert outcome.chunk_ids == ["c1"]
    assert outcome.paused is True
