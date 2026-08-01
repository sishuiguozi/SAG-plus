from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest


@dataclass
class NativeSpan:
    start_byte: int
    end_byte: int
    start_line: int
    end_line: int
    start_column: int = 0
    end_column: int = 1


@dataclass
class NativeItem:
    kind: str
    name: str
    span: NativeSpan
    children: list[NativeItem] = field(default_factory=list)
    visibility: str | None = None
    decorators: list[str] = field(default_factory=list)
    doc_comment: str | None = None
    signature: str | None = None
    body_span: NativeSpan | None = None


@dataclass
class NativeComment:
    text: str
    kind: str
    span: NativeSpan
    associated_node: str | None = None


@dataclass
class NativeChunk:
    content: str
    start_byte: int
    end_byte: int
    start_line: int
    end_line: int


@dataclass
class NativePoint:
    row: int
    column: int = 0


class NativeNode:
    def __init__(self, node_type, source, fragment, *, fields=None, children=None):
        self.type = node_type
        self.start_byte = source.index(fragment)
        self.end_byte = self.start_byte + len(fragment)
        self.start_point = NativePoint(source[: self.start_byte].count("\n"))
        self.end_point = NativePoint(source[: self.end_byte].count("\n"), 1)
        self._fields = fields or {}
        self.named_children = children or []

    def child_by_field_name(self, name):
        return self._fields.get(name)


def _span(source: str, fragment: str, *, occurrence: int = 0) -> NativeSpan:
    start = -1
    cursor = 0
    for _ in range(occurrence + 1):
        start = source.index(fragment, cursor)
        cursor = start + 1
    end = start + len(fragment)
    start_line = source[:start].count("\n")
    end_line = source[:end].count("\n")
    return NativeSpan(start, end, start_line, end_line)


@pytest.mark.parametrize(
    ("language", "fixture_name", "container_kind", "member_kind"),
    [
        ("python", "sample.py", "Class", "Function"),
        ("cpp", "sample.cpp", "Class", "Function"),
        ("typescript", "sample.ts", "Class", "Method"),
    ],
)
def test_parser_normalizes_symbols_with_stable_identity_and_ancestors(
    language, fixture_name, container_kind, member_kind
):
    from sag_api.code_ingest.parser import TreeSitterCodeParser

    source = (Path(__file__).parent / "fixtures" / "code_ingest" / fixture_name).read_text(
        encoding="utf-8"
    )
    class_fragment = source[source.index("class Service") :]
    class_fragment = class_fragment.split("\n\n", 1)[0]
    member_start = source.index("run(")
    member_line_start = source.rfind("\n", 0, member_start) + 1
    member_line_end = source.find("\n", member_start)
    member_fragment = source[member_line_start : member_line_end if member_line_end >= 0 else len(source)]
    structure = [
        NativeItem(
            container_kind,
            "Service",
            _span(source, class_fragment),
            children=[NativeItem(member_kind, "run", _span(source, member_fragment))],
        )
    ]

    def fake_process(_source, config):
        assert config.language == language
        return type(
            "Result",
            (),
            {
                "language": language,
                "structure": structure,
                "comments": [],
                "docstrings": [],
                "diagnostics": [],
                "chunks": [],
                "metrics": type("Metrics", (), {"error_count": 0})(),
            },
        )()

    parsed = TreeSitterCodeParser(process_fn=fake_process).parse(
        source,
        source_id="source-1",
        relative_path=f"repo/{fixture_name}",
        content_sha256="a" * 64,
        language=language,
    )

    container = parsed.symbols[0]
    member = container.children[0]
    assert container.kind == "class"
    assert container.identity == f"source-1:repo/{fixture_name}:class:Service"
    assert member.kind == "method"
    assert member.qualified_name == "Service.run"
    assert member.identity == f"source-1:repo/{fixture_name}:method:Service.run"
    assert member.ancestor_path == (f"repo/{fixture_name}", "Service")
    assert member.start_line > 0
    assert member.end_line >= member.start_line
    assert ":line:" not in member.identity


def test_parser_collects_comments_and_syntax_chunks():
    from sag_api.code_ingest.parser import TreeSitterCodeParser

    source = (Path(__file__).parent / "fixtures" / "code_ingest" / "sample.py").read_text(
        encoding="utf-8"
    )
    function_fragment = source[source.index("def long_function") :]
    function_span = _span(source, function_fragment)
    first_fragment = "    first = value + 1\n    second = first * 2\n"
    second_fragment = "    third = second - 3\n    return third\n"

    def fake_process(_source, _config):
        return type(
            "Result",
            (),
            {
                "language": "python",
                "structure": [NativeItem("Function", "long_function", function_span)],
                "comments": [
                    NativeComment("# Module documentation.", "Line", _span(source, "# Module documentation."))
                ],
                "docstrings": [],
                "diagnostics": [],
                "chunks": [
                    NativeChunk(first_fragment, *_byte_lines(source, first_fragment)),
                    NativeChunk(second_fragment, *_byte_lines(source, second_fragment)),
                ],
                "metrics": type("Metrics", (), {"error_count": 0})(),
            },
        )()

    parsed = TreeSitterCodeParser(process_fn=fake_process).parse(
        source,
        source_id="source-1",
        relative_path="repo/sample.py",
        content_sha256="b" * 64,
        language="python",
    )

    assert parsed.comments[0].text == "# Module documentation."
    assert len(parsed.syntax_chunks) == 2
    assert parsed.syntax_chunks[0].content.startswith("    first")


def _byte_lines(source: str, fragment: str) -> tuple[int, int, int, int]:
    span = _span(source, fragment)
    return span.start_byte, span.end_byte, span.start_line, span.end_line


def test_parser_rejects_an_unrecoverable_syntax_result():
    from sag_api.code_ingest.parser import CodeParseError, TreeSitterCodeParser

    def fake_process(_source, _config):
        diagnostic = type("Diagnostic", (), {"severity": "Error", "message": "fatal syntax"})()
        return type(
            "Result",
            (),
            {
                "language": "python",
                "structure": [],
                "comments": [],
                "docstrings": [],
                "diagnostics": [diagnostic],
                "chunks": [],
                "metrics": type("Metrics", (), {"error_count": 1})(),
            },
        )()

    with pytest.raises(CodeParseError, match="fatal syntax"):
        TreeSitterCodeParser(process_fn=fake_process).parse(
            "def broken(",
            source_id="source-1",
            relative_path="repo/broken.py",
            content_sha256="c" * 64,
            language="python",
        )


def test_ast_fallback_recovers_cpp_container_member_and_enum_names():
    from sag_api.code_ingest.parser import TreeSitterCodeParser

    source = "class Service { int run() { return 1; } };\nenum Mode { A, B };\n"
    service_name = NativeNode("type_identifier", source, "Service")
    run_name = NativeNode("identifier", source, "run")
    declarator = NativeNode("function_declarator", source, "run()", fields={"declarator": run_name})
    run = NativeNode(
        "function_definition",
        source,
        "int run() { return 1; }",
        fields={"declarator": declarator},
    )
    service = NativeNode(
        "class_specifier",
        source,
        "class Service { int run() { return 1; } }",
        fields={"name": service_name},
        children=[run],
    )
    mode_name = NativeNode("type_identifier", source, "Mode")
    mode = NativeNode(
        "enum_specifier",
        source,
        "enum Mode { A, B }",
        fields={"name": mode_name},
    )
    root = NativeNode("translation_unit", source, source, children=[service, mode])

    def fake_process(_source, _config):
        return type(
            "Result",
            (),
            {
                "language": "cpp",
                "structure": [],
                "comments": [],
                "docstrings": [],
                "diagnostics": [],
                "chunks": [],
                "metrics": type("Metrics", (), {"error_count": 0})(),
            },
        )()

    parsed = TreeSitterCodeParser(
        process_fn=fake_process,
        ast_root_fn=lambda _source, _language: root,
    ).parse(
        source,
        source_id="source-1",
        relative_path="repo/sample.cpp",
        content_sha256="e" * 64,
        language="cpp",
    )

    assert [(symbol.kind, symbol.name) for symbol in parsed.symbols] == [
        ("class", "Service"),
        ("enum", "Mode"),
    ]
    assert parsed.symbols[0].children[0].kind == "method"
    assert parsed.symbols[0].children[0].qualified_name == "Service.run"
