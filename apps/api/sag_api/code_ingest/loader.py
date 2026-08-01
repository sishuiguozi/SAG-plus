"""Bridge precomputed symbol chunks into zleap-sag's document loader."""

from __future__ import annotations

from pathlib import Path

from zleap.sag.modules.load.chunking.types import (
    BlockType,
    ChunkingResult,
    InputDocument,
    SectionDraft,
    StructuredBlock,
)
from zleap.sag.modules.load.config import DocumentLoadConfig
from zleap.sag.modules.load.loader import DocumentLoader
from zleap.sag.modules.load.parser import MarkdownParser

from sag_api.code_ingest.chunk_builder import SymbolChunkBuilder
from sag_api.code_ingest.parser import TreeSitterCodeParser
from sag_api.code_ingest.types import ParsedCodeDocument
from sag_api.parsing.service import PreparedDocument
from sag_api.parsing.text import read_text_file


class PrecomputedCodeParser(MarkdownParser):
    def __init__(
        self,
        document: ParsedCodeDocument,
        *,
        max_child_tokens: int = 1_000,
    ) -> None:
        super().__init__()
        self.document = document
        self._chunks = SymbolChunkBuilder(max_child_tokens=max_child_tokens).build(document)
        self._result: ChunkingResult | None = None

    async def parse_file_async(self, file_path: Path) -> tuple[str, int]:
        sections = [
            SectionDraft(
                order_index=chunk.rank,
                render_group_index=chunk.rank,
                heading=chunk.heading,
                content=chunk.content,
                raw_content=chunk.raw_content,
                section_type="CODE",
                metadata=dict(chunk.metadata),
            )
            for chunk in self._chunks
        ]
        blocks = [
            StructuredBlock(
                block_id=f"code-{chunk.rank}",
                block_type=BlockType.CODE,
                raw_content=chunk.raw_content,
                heading=chunk.heading,
                metadata=dict(chunk.metadata),
            )
            for chunk in self._chunks
        ]
        self._result = ChunkingResult(
            input_doc=InputDocument(
                content=self.document.source,
                source_path=file_path,
                is_markdown=False,
                metadata={
                    "relative_path": self.document.relative_path,
                    "content_sha256": self.document.content_sha256,
                    "code_language": self.document.language,
                },
            ),
            blocks=blocks,
            article_sections=sections,
            source_chunks=self._chunks,
        )
        return self.document.source, len(sections)

    def get_last_chunking_result(self) -> ChunkingResult:
        if self._result is None:
            raise RuntimeError("Code parser has not parsed a file yet")
        return self._result

    def extract_title(self, _content: str) -> str:
        return self.document.relative_path


class CodeDocumentLoader(DocumentLoader):
    """Use only the supplied precomputed parser, regardless of load config."""

    def __init__(self, parser: PrecomputedCodeParser) -> None:
        super().__init__(parser=parser)

    async def load(self, config: DocumentLoadConfig):
        self._enable_batch_indexing = config.enable_batch_indexing
        self._embedding_batch_size = config.embedding_batch_size
        self._es_bulk_index_size = config.es_bulk_index_size
        if not config.path:
            raise ValueError("Code file path is required")
        path = config.path if isinstance(config.path, Path) else Path(config.path)
        return await self.load_file(
            file_path=path,
            source_config_id=config.source_config_id,
            background=config.background or "",
            auto_vector=config.auto_vector,
            max_tokens=None,
            min_content_length=None,
            merge_short_sections=None,
            chunk_mode=None,
        )


def create_code_document_loader(
    prepared: PreparedDocument,
    *,
    source_id: str,
    max_child_tokens: int,
) -> CodeDocumentLoader:
    if prepared.provider != "tree_sitter":
        raise ValueError("Prepared document is not a Tree-sitter source file")
    if not prepared.relative_path or not prepared.content_sha256 or not prepared.code_language:
        raise ValueError("Prepared code metadata is incomplete")
    source = read_text_file(prepared.path).text
    parsed = TreeSitterCodeParser().parse(
        source,
        source_id=source_id,
        relative_path=prepared.relative_path,
        content_sha256=prepared.content_sha256,
        language=prepared.code_language,
    )
    return CodeDocumentLoader(
        PrecomputedCodeParser(parsed, max_child_tokens=max_child_tokens)
    )
