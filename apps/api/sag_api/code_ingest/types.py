"""Stable application-owned types for Tree-sitter analysis output."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class CodeSpan:
    start_byte: int
    end_byte: int
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class CodeComment:
    text: str
    kind: str
    span: CodeSpan


@dataclass(frozen=True, slots=True)
class SyntaxChunk:
    content: str
    start_byte: int
    end_byte: int
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class CodeSymbol:
    identity: str
    kind: str
    name: str
    qualified_name: str
    ancestor_path: tuple[str, ...]
    span: CodeSpan
    signature: str = ""
    visibility: str | None = None
    decorators: tuple[str, ...] = ()
    doc_comment: str | None = None
    comments: tuple[str, ...] = ()
    children: tuple[CodeSymbol, ...] = ()

    @property
    def start_line(self) -> int:
        return self.span.start_line

    @property
    def end_line(self) -> int:
        return self.span.end_line


@dataclass(frozen=True, slots=True)
class ParsedCodeDocument:
    source_id: str
    relative_path: str
    content_sha256: str
    language: str
    source: str
    symbols: tuple[CodeSymbol, ...] = ()
    comments: tuple[CodeComment, ...] = ()
    syntax_chunks: tuple[SyntaxChunk, ...] = ()
    diagnostics: tuple[str, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)
