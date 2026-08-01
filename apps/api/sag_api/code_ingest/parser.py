"""Normalize tree-sitter-language-pack output into stable SAG types."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sag_api.code_ingest.types import (
    CodeComment,
    CodeSpan,
    CodeSymbol,
    ParsedCodeDocument,
    SyntaxChunk,
)


class CodeParseError(ValueError):
    pass


@dataclass(slots=True)
class _AstSpan:
    start_byte: int
    end_byte: int
    start_line: int
    end_line: int
    start_column: int
    end_column: int


@dataclass(slots=True)
class _AstItem:
    kind: str
    name: str
    span: _AstSpan
    children: list[_AstItem] = field(default_factory=list)
    visibility: str | None = None
    decorators: list[str] = field(default_factory=list)
    doc_comment: str | None = None
    signature: str | None = None
    body_span: _AstSpan | None = None


class TreeSitterCodeParser:
    def __init__(
        self,
        *,
        process_fn: Callable[[str, Any], Any] | None = None,
        ast_root_fn: Callable[[str, str], Any] | None = None,
        syntax_chunk_bytes: int = 4_000,
    ) -> None:
        self._process_fn = process_fn
        self._ast_root_fn = ast_root_fn
        self.syntax_chunk_bytes = syntax_chunk_bytes

    def parse(
        self,
        source: str,
        *,
        source_id: str,
        relative_path: str,
        content_sha256: str,
        language: str,
    ) -> ParsedCodeDocument:
        result = self._process(source, language)
        native_structure = tuple(getattr(result, "structure", ()) or ())
        ast_root = self._ast_root(source, language)
        if ast_root is not None:
            ast_structure = _extract_ast_items(ast_root, source.encode("utf-8"))
            if ast_structure:
                native_structure = ast_structure
        diagnostics = tuple(
            str(getattr(diagnostic, "message", "Tree-sitter parse error"))
            for diagnostic in (getattr(result, "diagnostics", ()) or ())
        )
        error_count = int(getattr(getattr(result, "metrics", None), "error_count", 0) or 0)
        has_error_diagnostic = any(
            str(getattr(diagnostic, "severity", "")).casefold() == "error"
            for diagnostic in (getattr(result, "diagnostics", ()) or ())
        )
        if not native_structure and (error_count > 0 or has_error_diagnostic):
            raise CodeParseError(diagnostics[0] if diagnostics else "Unrecoverable Tree-sitter parse error")

        comments = tuple(
            CodeComment(
                text=str(getattr(comment, "text", "")).strip(),
                kind=_enum_name(getattr(comment, "kind", "line")),
                span=_normalize_span(getattr(comment, "span", None)),
            )
            for comment in (getattr(result, "comments", ()) or ())
            if getattr(comment, "span", None) is not None and str(getattr(comment, "text", "")).strip()
        )
        docstrings = tuple(getattr(result, "docstrings", ()) or ())
        source_bytes = source.encode("utf-8")
        symbols = tuple(
            self._normalize_symbol(
                item,
                source_id=source_id,
                relative_path=relative_path,
                source_bytes=source_bytes,
                comments=comments,
                docstrings=docstrings,
                parent_names=(),
                parent_kind=None,
            )
            for item in native_structure
            if getattr(item, "span", None) is not None and getattr(item, "name", None)
        )
        syntax_chunks = tuple(
            SyntaxChunk(
                content=str(getattr(chunk, "content", "")),
                start_byte=int(getattr(chunk, "start_byte", 0)),
                end_byte=int(getattr(chunk, "end_byte", 0)),
                start_line=int(getattr(chunk, "start_line", 0)) + 1,
                end_line=max(
                    int(getattr(chunk, "start_line", 0)) + 1,
                    int(getattr(chunk, "end_line", 0)) + 1,
                ),
            )
            for chunk in (getattr(result, "chunks", ()) or ())
            if str(getattr(chunk, "content", "")).strip()
        )
        return ParsedCodeDocument(
            source_id=source_id,
            relative_path=relative_path,
            content_sha256=content_sha256,
            language=str(getattr(result, "language", language) or language),
            source=source,
            symbols=symbols,
            comments=comments,
            syntax_chunks=syntax_chunks,
            diagnostics=diagnostics,
            metadata={"parse_error_count": error_count},
        )

    def _process(self, source: str, language: str):
        import tree_sitter_language_pack as pack

        config = pack.ProcessConfig(
            language=language,
            structure=True,
            imports=True,
            exports=True,
            comments=True,
            docstrings=True,
            symbols=True,
            diagnostics=True,
            chunk_max_size=self.syntax_chunk_bytes,
        )
        return (self._process_fn or pack.process)(source, config)

    def _ast_root(self, source: str, language: str):
        if self._ast_root_fn is not None:
            return self._ast_root_fn(source, language)
        if self._process_fn is not None:
            return None
        import tree_sitter_language_pack as pack

        return pack.get_parser(language).parse(source.encode("utf-8")).root_node

    def _normalize_symbol(
        self,
        item,
        *,
        source_id: str,
        relative_path: str,
        source_bytes: bytes,
        comments: tuple[CodeComment, ...],
        docstrings: tuple[Any, ...],
        parent_names: tuple[str, ...],
        parent_kind: str | None,
    ) -> CodeSymbol:
        name = str(item.name)
        raw_kind = _enum_name(getattr(item, "kind", "symbol"))
        kind = _normalize_kind(raw_kind, parent_kind)
        qualified_name = ".".join((*parent_names, name))
        span = _normalize_span(item.span)
        source_slice = source_bytes[span.start_byte : span.end_byte].decode("utf-8", errors="replace")
        signature = str(getattr(item, "signature", "") or "").strip() or _first_code_line(source_slice)
        symbol_comments = tuple(
            comment.text
            for comment in comments
            if _span_contains(span, comment.span)
            or comment.span.end_line in {span.start_line - 1, span.start_line}
        )
        doc_comment = str(getattr(item, "doc_comment", "") or "").strip() or None
        if doc_comment is None:
            doc_comment = _matching_docstring(docstrings, name, span)
        child_parent_names = (*parent_names, name)
        children = tuple(
            self._normalize_symbol(
                child,
                source_id=source_id,
                relative_path=relative_path,
                source_bytes=source_bytes,
                comments=comments,
                docstrings=docstrings,
                parent_names=child_parent_names,
                parent_kind=kind,
            )
            for child in (getattr(item, "children", ()) or ())
            if getattr(child, "span", None) is not None and getattr(child, "name", None)
        )
        return CodeSymbol(
            identity=f"{source_id}:{relative_path}:{kind}:{qualified_name}",
            kind=kind,
            name=name,
            qualified_name=qualified_name,
            ancestor_path=(relative_path, *parent_names),
            span=span,
            signature=signature,
            visibility=getattr(item, "visibility", None),
            decorators=tuple(getattr(item, "decorators", ()) or ()),
            doc_comment=doc_comment,
            comments=symbol_comments,
            children=children,
        )


def _enum_name(value: object) -> str:
    return str(value).strip().replace(" ", "_").casefold()


def _normalize_kind(raw_kind: str, parent_kind: str | None) -> str:
    aliases = {
        "function_definition": "function",
        "function_declaration": "function",
        "method_definition": "method",
        "method_declaration": "method",
        "type_alias": "type",
        "preproc_def": "macro",
    }
    kind = aliases.get(raw_kind, raw_kind)
    if kind == "function" and parent_kind in {"class", "struct", "interface", "trait"}:
        return "method"
    return kind


def _normalize_span(span) -> CodeSpan:
    if span is None:
        return CodeSpan(0, 0, 1, 1)
    start_line = int(getattr(span, "start_line", 0)) + 1
    native_end_line = int(getattr(span, "end_line", 0))
    end_column = int(getattr(span, "end_column", 1))
    end_line = native_end_line + (1 if end_column > 0 else 0)
    return CodeSpan(
        int(getattr(span, "start_byte", 0)),
        int(getattr(span, "end_byte", 0)),
        start_line,
        max(start_line, end_line),
    )


def _span_contains(outer: CodeSpan, inner: CodeSpan) -> bool:
    return outer.start_byte <= inner.start_byte and inner.end_byte <= outer.end_byte


def _first_code_line(source_slice: str) -> str:
    for line in source_slice.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _matching_docstring(docstrings: tuple[Any, ...], name: str, span: CodeSpan) -> str | None:
    for docstring in docstrings:
        text = str(getattr(docstring, "text", "") or "").strip()
        associated = str(getattr(docstring, "associated_item", "") or "")
        native_span = getattr(docstring, "span", None)
        if not text:
            continue
        if associated == name:
            return text
        if native_span is not None and _span_contains(span, _normalize_span(native_span)):
            return text
    return None


def _extract_ast_items(root, source_bytes: bytes) -> tuple[_AstItem, ...]:
    def walk(node) -> list[_AstItem]:
        items: list[_AstItem] = []
        for child in getattr(node, "named_children", ()) or ():
            kind = _classify_ast_node(str(getattr(child, "type", "")))
            if kind:
                name = _ast_node_name(child, source_bytes)
                if name:
                    item = _AstItem(
                        kind=kind,
                        name=name,
                        span=_ast_span(child),
                        children=walk(child),
                    )
                    items.append(item)
                    continue
            items.extend(walk(child))
        return items

    return tuple(walk(root))


def _classify_ast_node(node_type: str) -> str | None:
    normalized = node_type.casefold().replace("-", "_")
    if any(token in normalized for token in ("call", "declarator", "parameter", "argument")):
        return None
    patterns = (
        ("constructor", "constructor"),
        ("method", "method"),
        ("class", "class"),
        ("struct", "struct"),
        ("interface", "interface"),
        ("namespace", "namespace"),
        ("trait", "trait"),
        ("enum", "enum"),
        ("macro", "macro"),
    )
    structural_suffixes = ("declaration", "definition", "specifier", "item", "statement")
    for token, kind in patterns:
        if token in normalized and normalized.endswith(structural_suffixes):
            return kind
    if "function" in normalized and normalized.endswith(
        ("declaration", "definition", "item", "statement")
    ):
        return "function"
    if normalized in {"type_alias_declaration", "type_definition", "type_item"}:
        return "type"
    if normalized in {"module_declaration", "module_definition", "module_item"}:
        return "module"
    if normalized in {"preproc_def", "preproc_function_def"}:
        return "macro"
    return None


def _ast_node_name(node, source_bytes: bytes) -> str | None:
    for field_name in ("name", "declarator"):
        field_node = _child_by_field_name(node, field_name)
        if field_node is None:
            continue
        identifier = _find_identifier(field_node)
        if identifier is not None:
            return _node_text(identifier, source_bytes)
    identifier = _find_identifier(node)
    return _node_text(identifier, source_bytes) if identifier is not None else None


def _find_identifier(node):
    node_type = str(getattr(node, "type", "")).casefold()
    if node_type in {
        "constant",
        "field_identifier",
        "identifier",
        "namespace_identifier",
        "property_identifier",
        "type_identifier",
    } or node_type.endswith("_identifier"):
        return node
    for field_name in ("declarator", "name"):
        child = _child_by_field_name(node, field_name)
        if child is not None and child is not node:
            found = _find_identifier(child)
            if found is not None:
                return found
    for child in getattr(node, "named_children", ()) or ():
        found = _find_identifier(child)
        if found is not None:
            return found
    return None


def _child_by_field_name(node, name: str):
    method = getattr(node, "child_by_field_name", None)
    return method(name) if method is not None else None


def _node_text(node, source_bytes: bytes) -> str:
    start = int(getattr(node, "start_byte", 0))
    end = int(getattr(node, "end_byte", 0))
    return source_bytes[start:end].decode("utf-8", errors="replace").strip()


def _point_parts(point) -> tuple[int, int]:
    if hasattr(point, "row"):
        return int(point.row), int(point.column)
    return int(point[0]), int(point[1])


def _ast_span(node) -> _AstSpan:
    start_line, start_column = _point_parts(node.start_point)
    end_line, end_column = _point_parts(node.end_point)
    return _AstSpan(
        start_byte=int(node.start_byte),
        end_byte=int(node.end_byte),
        start_line=start_line,
        end_line=end_line,
        start_column=start_column,
        end_column=end_column,
    )
