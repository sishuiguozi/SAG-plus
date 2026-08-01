from __future__ import annotations

from pathlib import Path


def _parsed_sample():
    from sag_api.code_ingest.types import CodeComment, CodeSpan, CodeSymbol, ParsedCodeDocument, SyntaxChunk

    source = (Path(__file__).parent / "fixtures" / "code_ingest" / "sample.py").read_text(
        encoding="utf-8"
    )
    class_start = source.index("class Service")
    class_end = source.index("\n\n\ndef long_function")
    method_start = source.index("    def run")
    method_end = class_end
    long_start = source.index("def long_function")
    first_start = source.index("    first = value + 1")
    second_start = source.index("    third = second - 3")

    method = CodeSymbol(
        identity="source-1:repo/sample.py:method:Service.run",
        kind="method",
        name="run",
        qualified_name="Service.run",
        ancestor_path=("repo/sample.py", "Service"),
        span=CodeSpan(method_start, method_end, 5, 7),
        signature="def run(self, value: int) -> int:",
        comments=("# Return the value.",),
    )
    container = CodeSymbol(
        identity="source-1:repo/sample.py:class:Service",
        kind="class",
        name="Service",
        qualified_name="Service",
        ancestor_path=("repo/sample.py",),
        span=CodeSpan(class_start, class_end, 2, 7),
        signature="class Service:",
        doc_comment="Service documentation.",
        children=(method,),
    )
    long_function = CodeSymbol(
        identity="source-1:repo/sample.py:function:long_function",
        kind="function",
        name="long_function",
        qualified_name="long_function",
        ancestor_path=("repo/sample.py",),
        span=CodeSpan(long_start, len(source), 10, 14),
        signature="def long_function(value: int) -> int:",
    )
    return ParsedCodeDocument(
        source_id="source-1",
        relative_path="repo/sample.py",
        content_sha256="d" * 64,
        language="python",
        source=source,
        symbols=(container, long_function),
        comments=(CodeComment("# Module documentation.", "line", CodeSpan(0, 23, 1, 1)),),
        syntax_chunks=(
            SyntaxChunk(source[class_start:method_start], class_start, method_start, 2, 4),
            SyntaxChunk(source[method_start:class_end], method_start, class_end, 5, 7),
            SyntaxChunk(source[first_start:second_start], first_start, second_start, 11, 12),
            SyntaxChunk(source[second_start:], second_start, len(source), 13, 14),
        ),
    )


def test_builder_creates_file_class_and_long_function_parent_groups():
    from sag_api.code_ingest.chunk_builder import SymbolChunkBuilder

    chunks = SymbolChunkBuilder(max_child_tokens=16).build(_parsed_sample())
    by_symbol = {chunk.metadata.get("symbol_id"): chunk for chunk in chunks if chunk.metadata.get("symbol_id")}

    file_parent = next(chunk for chunk in chunks if chunk.metadata["symbol_kind"] == "file")
    class_parent = by_symbol["source-1:repo/sample.py:class:Service"]
    method_child = by_symbol["source-1:repo/sample.py:method:Service.run"]
    long_parent = by_symbol["source-1:repo/sample.py:function:long_function"]

    assert file_parent.metadata["chunk_type"] == "code_parent"
    assert class_parent.metadata["chunk_type"] == "code_parent"
    assert not any(
        chunk.metadata["symbol_kind"] == "statement_fragment"
        and chunk.metadata.get("parent_group") == class_parent.metadata["parent_group"]
        for chunk in chunks
    )
    assert method_child.metadata["chunk_type"] == "code_child"
    assert method_child.metadata["parent_group"] == class_parent.metadata["parent_group"]
    assert long_parent.metadata["chunk_type"] == "code_parent"
    long_children = [
        chunk
        for chunk in chunks
        if chunk.metadata.get("parent_group") == long_parent.metadata["parent_group"]
        and chunk.metadata["chunk_type"] == "code_child"
    ]
    assert len(long_children) == 2
    assert "first = value + 1" in long_children[0].content
    assert "return third" in long_children[1].content


def test_builder_metadata_is_revisioned_and_comment_extraction_contains_no_code_body():
    from sag_api.code_ingest.chunk_builder import SymbolChunkBuilder

    chunks = SymbolChunkBuilder(max_child_tokens=16).build(_parsed_sample())

    assert [chunk.rank for chunk in chunks] == list(range(len(chunks)))
    for chunk in chunks:
        assert chunk.chunk_type == "TEXT"
        assert chunk.metadata["chunk_source_type"] == "code"
        assert chunk.metadata["relative_path"] == "repo/sample.py"
        assert chunk.metadata["content_sha256"] == "d" * 64
        assert chunk.metadata["code_language"] == "python"
        assert chunk.metadata["start_line"] > 0
        assert chunk.metadata["end_line"] >= chunk.metadata["start_line"]

    extraction_text = "\n".join(
        chunk.metadata.get("llm_extraction_text", "") for chunk in chunks
    )
    assert "Module documentation" in extraction_text
    assert "Service documentation" in extraction_text
    assert "Return the value" in extraction_text
    assert "return value" not in extraction_text
