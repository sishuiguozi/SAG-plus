"""结构感知分块：代码块/表格保持完整，不按标点/段落切断。"""

import asyncio

import pytest

_MD = """# 引擎核心

```python
class Engine:
    def __init__(self, config):
        self.config = config
        self.parts = []

    def run(self):
        total = 0
        for part in self.parts:
            total += part.value
        return total

    def stop(self):
        self.parts.clear()
        print("stopped")
```

然后是表格：

| 名称 | 类型 | 说明 |
|------|------|------|
| alpha | int | 第一个参数，最大值 100 |
| beta  | str | 第二个参数，默认 "x" |
| gamma | bool | 开关，默认 False |
"""


async def _chunk(content: str, max_tokens: int = 120):
    from zleap.sag.modules.load.parser import MarkdownParser

    parser = MarkdownParser(max_tokens=max_tokens, chunk_mode="standard")
    result = await parser.parse_content_with_plan_async(content)
    return result.source_chunks


@pytest.mark.asyncio
async def test_structural_patch_keeps_code_and_table_intact():
    from sag_api.sag.chunking_compat import (
        install_structural_chunking_patch,
        uninstall_structural_chunking_patch,
    )

    try:
        install_structural_chunking_patch()
        chunks = await _chunk(_MD)
    finally:
        uninstall_structural_chunking_patch()

    code_chunks = [c for c in chunks if c.chunk_type == "CODE"]
    table_chunks = [c for c in chunks if c.chunk_type == "TABLE"]
    assert code_chunks, "应识别出 CODE chunk"
    assert table_chunks, "应识别出 TABLE chunk"
    assert "def __init__" in code_chunks[0].content
    assert "def stop" in code_chunks[0].content  # 同一代码块未被切断
    assert "| 名称 |" in table_chunks[0].content
    assert "gamma" in table_chunks[0].content  # 表格未被切断


@pytest.mark.asyncio
async def test_structural_large_code_split_by_lines():
    from sag_api.sag.chunking_compat import (
        install_structural_chunking_patch,
        uninstall_structural_chunking_patch,
    )

    big = "# 大代码\n\n```python\n" + "\n".join(
        f"def func_{i}(x):\n    return x + {i}" for i in range(60)
    ) + "\n```\n"
    try:
        install_structural_chunking_patch()
        chunks = await _chunk(big, max_tokens=200)
    finally:
        uninstall_structural_chunking_patch()

    code_chunks = [c for c in chunks if c.chunk_type == "CODE"]
    assert len(code_chunks) >= 2, "大代码块应按行切分为多个 chunk"
    for c in code_chunks:
        for line in c.content.splitlines():
            stripped = line.strip()
            if stripped.startswith("return"):
                assert line.startswith("    "), f"代码行缩进被破坏: {line!r}"


@pytest.mark.asyncio
async def test_plain_text_chunking_unchanged():
    """普通文本分块行为不受结构感知补丁影响。"""
    from sag_api.sag.chunking_compat import (
        install_structural_chunking_patch,
        uninstall_structural_chunking_patch,
    )

    md = "# 标题\n\n第一段文字，包含一些内容。\n\n第二段文字。"
    try:
        install_structural_chunking_patch()
        patched = await _chunk(md)
    finally:
        uninstall_structural_chunking_patch()
    plain = await _chunk(md)  # 未装补丁

    assert len(patched) == len(plain)
    assert all(c.chunk_type == "TEXT" for c in patched)
    assert patched[0].content == plain[0].content
