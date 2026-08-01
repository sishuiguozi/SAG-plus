"""结构感知分块补丁：代码块 / 表格作为不可分割单元，避免被拦腰切断。

zleap-sag 的 MarkdownBlockParser 只按标题拆 TEXT block，代码块与表格会被
当作普通文本，随后被文本切分器（按标点/换行）和组装器（按段落）切断，
导致检索碎片化。本补丁在应用层增强三步：

1. Block 识别：把 fenced code block 与连续 | 表格行 识别为 CODE / TABLE block；
2. Section 生成：CODE / TABLE block 整块生成 Section（不按标点切分）；
3. Chunk 组装：超长 CODE / TABLE 按行切分（保持每行/表格行完整），不按段落切。
"""

from __future__ import annotations

import re
from typing import List, Tuple

from sag_api.core.logging import get_logger
from zleap.sag.modules.load.chunking.chunker.markdown import MarkdownTextChunker
from zleap.sag.modules.load.chunking.parser.markdown import MarkdownBlockParser
from zleap.sag.modules.load.chunking.types import (
    BlockType,
    ChunkDraft,
    InputDocument,
    SectionDraft,
    StructuredBlock,
)

log = get_logger("sag.chunking_structural")

# 已安装状态与原始引用（便于恢复/测试）
_patch_installed = False
_original: dict = {}


# ───────────────────────── 1. 结构感知 Block 识别 ─────────────────────────
class StructuralMarkdownBlockParser(MarkdownBlockParser):
    """在标题拆分基础上，额外识别代码块与表格为独立结构块。"""

    FENCE_START_RE = re.compile(r"^(\s*)(```+|~~~+)[^\n]*$", re.MULTILINE)
    FENCE_CLOSE_RE = re.compile(r"^\s*(```+|~~~+)[^\n]*$", re.MULTILINE)
    TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
    TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")

    def parse_blocks(self, doc: InputDocument) -> List[StructuredBlock]:
        # A6：正则自定义分块（document_chunk_regex 非空时按正则切段）
        from sag_api.core.config import settings

        regex_pattern = (getattr(settings, "document_chunk_regex", None) or "").strip()
        if regex_pattern:
            return self._parse_blocks_by_regex(doc, regex_pattern)
        text = doc.content or ""
        protected = self._find_protected_spans(text)
        headings = self._heading_positions(text, [])
        blocks: List[StructuredBlock] = []
        cursor = 0
        counter = 0

        for start, end, btype in protected:
            if start > cursor:
                seg = text[cursor:start]
                if seg.strip():
                    part, counter = self._build_text_blocks(
                        text=seg, start=cursor, end=start, headings=headings, counter=counter
                    )
                    blocks.extend(part)
            raw = text[start:end]
            if raw.strip():
                blocks.append(
                    StructuredBlock(
                        block_id=f"{btype.name.lower()}-{counter}",
                        block_type=btype,
                        raw_content=raw,
                        heading=self._resolve_heading(headings, start),
                        start_index=start,
                        end_index=end,
                        metadata={"protected": True, "structural": True},
                    )
                )
                counter += 1
            cursor = end

        if cursor < len(text) and text[cursor:].strip():
            tail = text[cursor:]
            part, _ = self._build_text_blocks(
                text=tail, start=cursor, end=len(text), headings=headings, counter=counter
            )
            blocks.extend(part)

        return [b for b in blocks if b.raw_content.strip() != ""]

    def _parse_blocks_by_regex(
        self,
        doc: InputDocument,
        regex_pattern: str,
    ) -> List[StructuredBlock]:
        """按用户自定义正则切分段落（保护代码块/表格不被切开）。"""
        text = doc.content or ""
        protected = self._find_protected_spans(text)
        headings = self._heading_positions(text, [])
        blocks: List[StructuredBlock] = []
        cursor = 0
        counter = 0

        def _emit_text(start: int, end: int) -> None:
            nonlocal counter
            seg = text[start:end]
            if not seg.strip():
                return
            parts = re.split(regex_pattern, seg)
            pos = start
            for part in parts:
                if not part.strip():
                    pos += len(part)
                    continue
                blocks.append(
                    StructuredBlock(
                        block_id=f"text-{counter}",
                        block_type=BlockType.TEXT,
                        raw_content=part,
                        heading=self._resolve_heading(headings, pos),
                        start_index=pos,
                        end_index=pos + len(part),
                        metadata={"regex": True},
                    )
                )
                counter += 1
                pos += len(part)

        for start, end, btype in protected:
            if start > cursor:
                _emit_text(cursor, start)
            raw = text[start:end]
            if raw.strip():
                blocks.append(
                    StructuredBlock(
                        block_id=f"{btype.name.lower()}-{counter}",
                        block_type=btype,
                        raw_content=raw,
                        heading=self._resolve_heading(headings, start),
                        start_index=start,
                        end_index=end,
                        metadata={"protected": True, "structural": True},
                    )
                )
                counter += 1
            cursor = end
        if cursor < len(text) and text[cursor:].strip():
            _emit_text(cursor, len(text))
        return [b for b in blocks if b.raw_content.strip() != ""]

    def _find_protected_spans(self, text: str) -> List[Tuple[int, int, BlockType]]:
        spans: List[Tuple[int, int, BlockType]] = []
        for m in self.FENCE_START_RE.finditer(text):
            fence = m.group(2)
            close = self.FENCE_CLOSE_RE.search(text[m.end():])
            if close:
                spans.append((m.start(), m.end() + close.end(), BlockType.CODE))
        spans = self._dedupe_spans(spans)

        # 表格：连续 | 行组成的块（至少 2 行，且包含分隔行）
        lines = text.splitlines(keepends=True)
        line_starts = self._line_starts(text)
        i = 0
        table_spans: List[Tuple[int, int, BlockType]] = []
        while i < len(lines):
            if self.TABLE_ROW_RE.match(lines[i]):
                j = i
                rows: List[str] = []
                while j < len(lines) and self.TABLE_ROW_RE.match(lines[j]):
                    rows.append(lines[j])
                    j += 1
                # 要求 ≥2 行且含分隔行（|---|---|）
                has_sep = any(self.TABLE_SEP_RE.match(row) for row in rows[1:])
                if len(rows) >= 2 and has_sep:
                    start = line_starts[i]
                    end = line_starts[j - 1] + len(lines[j - 1])
                    table_spans.append((start, end, BlockType.TABLE))
                i = j
            else:
                i += 1

        spans.extend(self._dedupe_spans(table_spans))
        return self._dedupe_spans(sorted(spans, key=lambda s: s[0]))

    @staticmethod
    def _dedupe_spans(spans: List[Tuple[int, int, BlockType]]) -> List[Tuple[int, int, BlockType]]:
        """去掉重叠区间（保留先识别到的；代码块优先于表格）。"""
        spans = sorted(spans, key=lambda s: (s[0], -(s[1] - s[0])))
        result: List[Tuple[int, int, BlockType]] = []
        for start, end, btype in spans:
            if result and start < result[-1][1]:
                continue
            result.append((start, end, btype))
        return result

    @staticmethod
    def _line_starts(text: str) -> List[int]:
        starts = [0]
        for m in re.finditer("\n", text):
            starts.append(m.end())
        return starts


# ───────────────────────── 2. 结构感知 Section 生成 ─────────────────────────
class StructuralMarkdownTextChunker(MarkdownTextChunker):
    """CODE / TABLE block 整块生成 Section（不按标点切分）；TEXT 走原逻辑。"""

    async def build_sections(
        self,
        block: StructuredBlock,
        order_start: int,
        render_group_index: int,
    ) -> List[SectionDraft]:
        if block.block_type in (BlockType.CODE, BlockType.TABLE):
            return [
                SectionDraft(
                    order_index=order_start,
                    render_group_index=render_group_index,
                    heading=block.heading,
                    content=block.raw_content,
                    raw_content=block.raw_content,
                    section_type=block.block_type.value,
                    metadata={
                        "block_type": block.block_type.value,
                        "render_format": "markdown_text",
                        "block_id": block.block_id,
                        "assemble_policy": "structural",
                    },
                )
            ]
        return await super().build_sections(block, order_start, render_group_index)


# ───────────────────────── 3. 结构感知 Chunk 组装 ─────────────────────────
def _structural_split_large_section(self, section: SectionDraft) -> List[SectionDraft]:
    """CODE / TABLE 大 section 按行切分（保持每行完整），其余按段落切。"""
    if section.section_type in (BlockType.CODE.value, BlockType.TABLE.value):
        return _split_by_lines(self, section)
    return _original["split_large_section"](self, section)


def _split_by_lines(self, section: SectionDraft) -> List[SectionDraft]:
    from zleap.sag.modules.load.chunking.assembler.generic import PolicyBasedSourceChunkAssembler

    content = section.content.strip()
    if not content:
        return [section]

    lines = content.splitlines()
    units: List[SectionDraft] = []
    current_lines: List[str] = []
    current_tokens = 0
    max_tokens = self.source_chunk_max_tokens

    for line in lines:
        line_tokens = self.token_estimator.estimate_tokens(line)
        # 单行超长：独立成 unit（不硬切）
        if line_tokens > max_tokens:
            if current_lines:
                units.append(PolicyBasedSourceChunkAssembler._clone_section(section, "\n".join(current_lines)))
                current_lines = []
                current_tokens = 0
            units.append(PolicyBasedSourceChunkAssembler._clone_section(section, line))
            continue
        if not current_lines:
            current_lines = [line]
            current_tokens = line_tokens
        elif current_tokens + line_tokens <= max_tokens:
            current_lines.append(line)
            current_tokens += line_tokens
        else:
            units.append(PolicyBasedSourceChunkAssembler._clone_section(section, "\n".join(current_lines)))
            current_lines = [line]
            current_tokens = line_tokens

    if current_lines:
        units.append(PolicyBasedSourceChunkAssembler._clone_section(section, "\n".join(current_lines)))
    return units if units else [section]


def _structural_build_chunk(self, sections: List[SectionDraft]) -> ChunkDraft:
    """CODE / TABLE chunk 保留原始缩进与结构（不 strip 各行）。"""
    if not sections or sections[0].section_type not in (BlockType.CODE.value, BlockType.TABLE.value):
        return _original["build_chunk"](self, sections)

    heading = next((s.heading for s in sections if s.heading), "")
    content = "\n".join(s.content for s in sections).strip("\n")
    raw_content = "".join(s.raw_content for s in sections)
    section_order_indices = [s.order_index for s in sections]
    chunk_type = sections[0].section_type
    render_group_indices = sorted({s.render_group_index for s in sections})
    return ChunkDraft(
        rank=0,
        heading=heading,
        content=content if content else raw_content,
        raw_content=raw_content,
        chunk_type=chunk_type,
        section_order_indices=section_order_indices,
        metadata={
            "section_order_indices": section_order_indices,
            "render_group_index": render_group_indices[0],
            "render_group_indices": render_group_indices,
            "chunk_type": chunk_type,
        },
    )


def _structural_assemble_chunks(self, doc: InputDocument, sections: List[SectionDraft]):
    """结构感知组装：CODE / TABLE section 强制独立成 chunk（不与其他 section 聚合）。"""
    # A4：父子分块（增量启用）——仅当 document_chunk_mode == "parent_child" 时切换。
    from sag_api.core.config import settings

    if getattr(settings, "document_chunk_mode", None) == "parent_child":
        return _parent_child_assemble_chunks(self, doc, sections)

    chunks: List[ChunkDraft] = []
    current: List[SectionDraft] = []
    current_tokens = 0
    structural_types = (BlockType.CODE.value, BlockType.TABLE.value)

    for section in sections:
        section_tokens = self._section_tokens(section)
        is_structural = section.section_type in structural_types

        if is_structural:
            # 结构块：先冲刷当前聚合，再独立成 chunk（大块按行切）
            if current:
                chunks.append(_original["build_chunk"](self, current))
                current = []
                current_tokens = 0
            if section_tokens > self.standalone_block_max_tokens:
                for unit in self._split_large_section(section):
                    chunks.append(_structural_build_chunk(self, [unit]))
            else:
                chunks.append(_structural_build_chunk(self, [section]))
            continue

        # ── 以下为原组装逻辑（非结构块）──
        if self.heading_strict and current and section.heading != current[0].heading:
            chunks.append(_original["build_chunk"](self, current))
            current = []
            current_tokens = 0

        if section_tokens > self.standalone_block_max_tokens:
            if current:
                chunks.append(_original["build_chunk"](self, current))
                current = []
                current_tokens = 0
            for unit in self._split_large_section(section):
                chunks.append(_original["build_chunk"](self, [unit]))
            continue

        if not current:
            current = [section]
            current_tokens = section_tokens
        elif current_tokens + section_tokens <= self.source_chunk_max_tokens:
            current.append(section)
            current_tokens += section_tokens
        else:
            chunks.append(_original["build_chunk"](self, current))
            current = [section]
            current_tokens = section_tokens

    if current:
        chunks.append(_original["build_chunk"](self, current))

    for idx, chunk in enumerate(chunks):
        chunk.rank = idx
    return chunks


# ───────────────────────── A4 父子分块 ─────────────────────────
def _split_by_lines_cap(self, section: SectionDraft, max_tokens: int) -> List[SectionDraft]:
    """按行切分（保持每行完整），以 max_tokens 为上限（父块分组用）。"""
    from zleap.sag.modules.load.chunking.assembler.generic import PolicyBasedSourceChunkAssembler

    content = (section.content or "").strip()
    if not content:
        return [section]
    lines = content.splitlines()
    units: List[SectionDraft] = []
    current_lines: List[str] = []
    current_tokens = 0
    for line in lines:
        line_tokens = self.token_estimator.estimate_tokens(line)
        if line_tokens > max_tokens:
            if current_lines:
                units.append(
                    PolicyBasedSourceChunkAssembler._clone_section(section, "\n".join(current_lines))
                )
                current_lines = []
                current_tokens = 0
            units.append(PolicyBasedSourceChunkAssembler._clone_section(section, line))
            continue
        if not current_lines:
            current_lines = [line]
            current_tokens = line_tokens
        elif current_tokens + line_tokens <= max_tokens:
            current_lines.append(line)
            current_tokens += line_tokens
        else:
            units.append(
                PolicyBasedSourceChunkAssembler._clone_section(section, "\n".join(current_lines))
            )
            current_lines = [line]
            current_tokens = line_tokens
    if current_lines:
        units.append(
            PolicyBasedSourceChunkAssembler._clone_section(section, "\n".join(current_lines))
        )
    return units if units else [section]


def _parent_child_split_group(self, group: List[SectionDraft], group_index: int) -> List[ChunkDraft]:
    """父组内按 source_chunk_max_tokens 切子块；结构块（CODE/TABLE）按行切。"""
    child_chunks: List[ChunkDraft] = []
    structural_types = (BlockType.CODE.value, BlockType.TABLE.value)
    is_structural = bool(group) and group[0].section_type in structural_types
    if is_structural:
        for section in group:
            if self._section_tokens(section) > self.standalone_block_max_tokens:
                for unit in self._split_large_section(section):
                    child_chunks.append(_structural_build_chunk(self, [unit]))
            else:
                child_chunks.append(_structural_build_chunk(self, [section]))
    else:
        current: List[SectionDraft] = []
        current_tokens = 0
        for section in group:
            section_tokens = self._section_tokens(section)
            if not current:
                current = [section]
                current_tokens = section_tokens
            elif current_tokens + section_tokens <= self.source_chunk_max_tokens:
                current.append(section)
                current_tokens += section_tokens
            else:
                child_chunks.append(_original["build_chunk"](self, current))
                current = [section]
                current_tokens = section_tokens
        if current:
            child_chunks.append(_original["build_chunk"](self, current))

    for chunk in child_chunks:
        meta = dict(chunk.metadata or {})
        meta["chunk_type"] = "child"
        meta["chunk_source_type"] = chunk.chunk_type
        meta["parent_group"] = group_index
        chunk.metadata = meta
    return child_chunks


def _parent_child_assemble_chunks(
    self, doc: InputDocument, sections: List[SectionDraft]
) -> List[ChunkDraft]:
    """父子分块（A4，增量启用）：父块提供上下文，子块负责精确检索。

    - 父块：按 parent_chunk_max_tokens 聚合段落，metadata.chunk_type="parent"；
    - 子块：父组内按 source_chunk_max_tokens 切分，metadata.chunk_type="child"；
    - 关联：metadata["parent_group"] 序号，入库时由 sag_api.sag.parent_child 回填 parent_id；
    - 结构块（CODE/TABLE）：独立父组、子块按行切分，保持结构完整。
    """
    from sag_api.core.config import settings

    parent_max = int(getattr(settings, "parent_chunk_max_tokens", 1024) or 1024)
    chunks: List[ChunkDraft] = []
    parent_groups: List[List[SectionDraft]] = []
    current: List[SectionDraft] = []
    current_tokens = 0
    structural_types = (BlockType.CODE.value, BlockType.TABLE.value)

    for section in sections:
        section_tokens = self._section_tokens(section)
        is_structural = section.section_type in structural_types

        if is_structural:
            if current:
                parent_groups.append(current)
                current = []
                current_tokens = 0
            if section_tokens > parent_max:
                for unit in _split_by_lines_cap(self, section, parent_max):
                    parent_groups.append([unit])
            else:
                parent_groups.append([section])
            continue

        if not current:
            current = [section]
            current_tokens = section_tokens
        elif current_tokens + section_tokens <= parent_max:
            current.append(section)
            current_tokens += section_tokens
        else:
            parent_groups.append(current)
            current = [section]
            current_tokens = section_tokens

    if current:
        parent_groups.append(current)

    for group_index, group in enumerate(parent_groups):
        parent = _structural_build_chunk(self, group)
        parent.metadata = dict(parent.metadata or {})
        parent.metadata["chunk_type"] = "parent"
        parent.metadata["chunk_source_type"] = parent.chunk_type
        parent.metadata["parent_group"] = group_index
        chunks.append(parent)
        chunks.extend(_parent_child_split_group(self, group, group_index))

    for idx, chunk in enumerate(chunks):
        chunk.rank = idx
    return chunks


# ───────────────────────── 安装 / 卸载 ─────────────────────────
def install_structural_chunking_patch() -> None:
    """启用结构感知分块（代码块 / 表格不被切断）。"""
    global _patch_installed, _original

    import zleap.sag.modules.load.chunking as zl_chunking
    import zleap.sag.modules.load.chunking.assembler.generic as zl_assembler
    import zleap.sag.modules.load.chunking.chunker.markdown as zl_chunker_md
    import zleap.sag.modules.load.chunking.parser.markdown as zl_parser_md
    import zleap.sag.modules.load.parser as zl_parser

    if _patch_installed:
        return

    _original["parser_module"] = zl_parser_md.MarkdownBlockParser
    _original["parser_module_parser"] = zl_parser.MarkdownBlockParser
    _original["chunker"] = zl_chunker_md.MarkdownTextChunker
    _original["split_large_section"] = zl_assembler.PolicyBasedSourceChunkAssembler._split_large_section
    _original["build_chunk"] = zl_assembler.PolicyBasedSourceChunkAssembler._build_chunk
    _original["assemble_chunks"] = zl_assembler.PolicyBasedSourceChunkAssembler.assemble_chunks
    _original["chunking_parser"] = getattr(zl_chunking, "MarkdownBlockParser", None)
    _original["chunking_chunker"] = getattr(zl_chunking, "MarkdownTextChunker", None)

    zl_parser_md.MarkdownBlockParser = StructuralMarkdownBlockParser
    zl_parser.MarkdownBlockParser = StructuralMarkdownBlockParser
    zl_chunker_md.MarkdownTextChunker = StructuralMarkdownTextChunker
    zl_assembler.PolicyBasedSourceChunkAssembler._split_large_section = _structural_split_large_section
    zl_assembler.PolicyBasedSourceChunkAssembler._build_chunk = _structural_build_chunk
    zl_assembler.PolicyBasedSourceChunkAssembler.assemble_chunks = _structural_assemble_chunks
    if _original["chunking_parser"] is not None:
        zl_chunking.MarkdownBlockParser = StructuralMarkdownBlockParser
    if _original["chunking_chunker"] is not None:
        zl_chunking.MarkdownTextChunker = StructuralMarkdownTextChunker

    _patch_installed = True
    log.info("结构感知分块已启用（代码块/表格保持完整）")


def uninstall_structural_chunking_patch() -> None:
    """恢复 zleap-sag 原生分块（主要用于测试）。"""
    global _patch_installed
    if not _patch_installed:
        return

    import zleap.sag.modules.load.chunking as zl_chunking
    import zleap.sag.modules.load.chunking.assembler.generic as zl_assembler
    import zleap.sag.modules.load.chunking.chunker.markdown as zl_chunker_md
    import zleap.sag.modules.load.chunking.parser.markdown as zl_parser_md
    import zleap.sag.modules.load.parser as zl_parser

    zl_parser_md.MarkdownBlockParser = _original["parser_module"]
    zl_parser.MarkdownBlockParser = _original["parser_module_parser"]
    zl_chunker_md.MarkdownTextChunker = _original["chunker"]
    zl_assembler.PolicyBasedSourceChunkAssembler._split_large_section = _original["split_large_section"]
    zl_assembler.PolicyBasedSourceChunkAssembler._build_chunk = _original["build_chunk"]
    zl_assembler.PolicyBasedSourceChunkAssembler.assemble_chunks = _original["assemble_chunks"]
    if _original["chunking_parser"] is not None:
        zl_chunking.MarkdownBlockParser = _original["chunking_parser"]
    if _original["chunking_chunker"] is not None:
        zl_chunking.MarkdownTextChunker = _original["chunking_chunker"]

    _patch_installed = False
    log.info("结构感知分块已卸载（恢复原生分块）")
