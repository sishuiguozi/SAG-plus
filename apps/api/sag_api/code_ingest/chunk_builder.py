"""Convert normalized code symbols into zleap-compatible parent/child chunks."""

from __future__ import annotations

from collections.abc import Callable

from zleap.sag.modules.load.chunking.types import ChunkDraft

from sag_api.code_ingest.types import CodeSymbol, ParsedCodeDocument, SyntaxChunk

_CONTAINER_KINDS = {"class", "struct", "interface", "namespace", "trait", "module"}
_CALLABLE_KINDS = {"constructor", "function", "method"}


class SymbolChunkBuilder:
    def __init__(
        self,
        *,
        max_child_tokens: int = 1_000,
        token_estimator: Callable[[str], int] | None = None,
    ) -> None:
        self.max_child_tokens = max_child_tokens
        self._estimate = token_estimator or (lambda text: max(1, len(text.encode("utf-8")) // 4))

    def build(self, document: ParsedCodeDocument) -> list[ChunkDraft]:
        chunks: list[ChunkDraft] = []
        file_group = f"{document.source_id}:{document.relative_path}:file"
        file_content = self._file_context(document)
        chunks.append(
            self._draft(
                document,
                rank=0,
                heading=document.relative_path,
                content=file_content,
                symbol_id=file_group,
                symbol_kind="file",
                qualified_name=document.relative_path,
                ancestor_path=(document.relative_path,),
                parent_group=file_group,
                chunk_role="code_parent",
                start_line=1,
                end_line=max(1, document.source.count("\n") + 1),
                extraction_text="\n".join(comment.text for comment in document.comments),
            )
        )
        for symbol in document.symbols:
            self._append_symbol(document, symbol, file_group, chunks)
        for rank, chunk in enumerate(chunks):
            chunk.rank = rank
            chunk.section_order_indices = [rank]
        return chunks

    def _append_symbol(
        self,
        document: ParsedCodeDocument,
        symbol: CodeSymbol,
        parent_group: str,
        chunks: list[ChunkDraft],
    ) -> None:
        source_text = _slice_source(document.source, symbol.span.start_byte, symbol.span.end_byte)
        syntax_chunks = self._contained_syntax_chunks(document.syntax_chunks, symbol)
        is_long = (
            symbol.kind in _CALLABLE_KINDS
            and self._estimate(source_text) > self.max_child_tokens
            and len(syntax_chunks) > 1
        )
        is_container = symbol.kind in _CONTAINER_KINDS
        extraction_text = _extraction_text(symbol)

        if is_container or is_long:
            group = symbol.identity
            chunks.append(
                self._draft(
                    document,
                    rank=len(chunks),
                    heading=symbol.qualified_name,
                    content=self._symbol_context(document, symbol),
                    symbol_id=symbol.identity,
                    symbol_kind=symbol.kind,
                    qualified_name=symbol.qualified_name,
                    ancestor_path=symbol.ancestor_path,
                    parent_group=group,
                    chunk_role="code_parent",
                    start_line=symbol.start_line,
                    end_line=symbol.end_line,
                    extraction_text=extraction_text,
                )
            )
            if is_long:
                self._append_syntax_children(document, symbol, syntax_chunks, group, chunks)
            for child in symbol.children:
                self._append_symbol(document, child, group, chunks)
            return

        chunks.append(
            self._draft(
                document,
                rank=len(chunks),
                heading=symbol.qualified_name,
                content=source_text,
                symbol_id=symbol.identity,
                symbol_kind=symbol.kind,
                qualified_name=symbol.qualified_name,
                ancestor_path=symbol.ancestor_path,
                parent_group=parent_group,
                chunk_role="code_child",
                start_line=symbol.start_line,
                end_line=symbol.end_line,
                extraction_text=extraction_text,
            )
        )
        for child in symbol.children:
            self._append_symbol(document, child, parent_group, chunks)

    def _append_syntax_children(
        self,
        document: ParsedCodeDocument,
        symbol: CodeSymbol,
        syntax_chunks: tuple[SyntaxChunk, ...],
        parent_group: str,
        chunks: list[ChunkDraft],
    ) -> None:
        for index, fragment in enumerate(syntax_chunks, start=1):
            chunks.append(
                self._draft(
                    document,
                    rank=len(chunks),
                    heading=f"{symbol.qualified_name} · {index}",
                    content=fragment.content,
                    symbol_id=f"{symbol.identity}#fragment:{index}",
                    symbol_kind="statement_fragment",
                    qualified_name=symbol.qualified_name,
                    ancestor_path=(*symbol.ancestor_path, symbol.name),
                    parent_group=parent_group,
                    chunk_role="code_child",
                    start_line=fragment.start_line,
                    end_line=fragment.end_line,
                    extraction_text="",
                    extra={"parent_symbol_id": symbol.identity, "fragment_index": index},
                )
            )

    @staticmethod
    def _contained_syntax_chunks(
        syntax_chunks: tuple[SyntaxChunk, ...], symbol: CodeSymbol
    ) -> tuple[SyntaxChunk, ...]:
        return tuple(
            chunk
            for chunk in syntax_chunks
            if symbol.span.start_byte <= chunk.start_byte
            and chunk.end_byte <= symbol.span.end_byte
            and chunk.content.strip()
        )

    @staticmethod
    def _file_context(document: ParsedCodeDocument) -> str:
        symbols = "\n".join(
            f"- {symbol.kind} {symbol.qualified_name}" for symbol in document.symbols
        )
        return (
            f"File: {document.relative_path}\n"
            f"Language: {document.language}\n"
            f"Symbols:\n{symbols or '- none'}"
        )

    @staticmethod
    def _symbol_context(document: ParsedCodeDocument, symbol: CodeSymbol) -> str:
        ancestors = " > ".join(symbol.ancestor_path)
        return (
            f"File: {document.relative_path}\n"
            f"Symbol: {symbol.kind} {symbol.qualified_name}\n"
            f"Ancestors: {ancestors}\n"
            f"Signature: {symbol.signature}"
        )

    @staticmethod
    def _draft(
        document: ParsedCodeDocument,
        *,
        rank: int,
        heading: str,
        content: str,
        symbol_id: str,
        symbol_kind: str,
        qualified_name: str,
        ancestor_path: tuple[str, ...],
        parent_group: str,
        chunk_role: str,
        start_line: int,
        end_line: int,
        extraction_text: str,
        extra: dict[str, object] | None = None,
    ) -> ChunkDraft:
        metadata: dict[str, object] = {
            "chunk_type": chunk_role,
            "chunk_source_type": "code",
            "parent_group": parent_group,
            "symbol_id": symbol_id,
            "symbol_kind": symbol_kind,
            "qualified_name": qualified_name,
            "ancestor_path": list(ancestor_path),
            "relative_path": document.relative_path,
            "content_sha256": document.content_sha256,
            "code_language": document.language,
            "start_line": max(1, start_line),
            "end_line": max(start_line, end_line),
        }
        if extraction_text.strip():
            metadata["llm_extraction_text"] = extraction_text.strip()
        if extra:
            metadata.update(extra)
        return ChunkDraft(
            rank=rank,
            heading=heading,
            content=content.strip(),
            raw_content=content,
            chunk_type="TEXT",
            section_order_indices=[rank],
            metadata=metadata,
        )


def _slice_source(source: str, start_byte: int, end_byte: int) -> str:
    return source.encode("utf-8")[start_byte:end_byte].decode("utf-8", errors="replace")


def _extraction_text(symbol: CodeSymbol) -> str:
    parts = [symbol.doc_comment or "", *symbol.comments]
    return "\n".join(part.strip() for part in parts if part and part.strip())
