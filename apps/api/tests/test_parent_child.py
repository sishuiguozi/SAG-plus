"""A4 父子分块：切分关联、入库 parent_id 回填、检索父块上下文（增量安全）。"""

import uuid

import pytest
import pytest_asyncio

_MD = """# 第一章

这是第一段文字，介绍背景信息。

这是第二段，继续讨论细节。

```python
class Demo:
    def run(self):
        return 42
```
"""


async def _chunk(content: str, max_tokens: int = 120):
    from zleap.sag.modules.load.parser import MarkdownParser

    parser = MarkdownParser(max_tokens=max_tokens, chunk_mode="standard")
    result = await parser.parse_content_with_plan_async(content)
    return result.source_chunks


@pytest.mark.asyncio
async def test_parent_child_chunking_generates_parent_and_child():
    from sag_api.core.config import settings
    from sag_api.sag.chunking_compat import (
        install_structural_chunking_patch,
        uninstall_structural_chunking_patch,
    )

    old_mode = settings.document_chunk_mode
    old_max = settings.parent_chunk_max_tokens
    settings.document_chunk_mode = "parent_child"
    settings.parent_chunk_max_tokens = 512
    try:
        install_structural_chunking_patch()
        chunks = await _chunk(_MD)
    finally:
        uninstall_structural_chunking_patch()
        settings.document_chunk_mode = old_mode
        settings.parent_chunk_max_tokens = old_max

    parents = [c for c in chunks if (c.metadata or {}).get("chunk_type") == "parent"]
    children = [c for c in chunks if (c.metadata or {}).get("chunk_type") == "child"]
    assert parents, "应生成父块"
    assert children, "应生成子块"
    assert len(parents) == len(set((c.metadata or {})["parent_group"] for c in parents))
    child_groups = {(c.metadata or {})["parent_group"] for c in children}
    parent_groups = {(c.metadata or {})["parent_group"] for c in parents}
    assert child_groups <= parent_groups, "子块必须能关联到父块"
    # 代码块结构保持完整
    code_child = [c for c in children if (c.metadata or {}).get("chunk_source_type") == "CODE"]
    assert code_child and "def run" in code_child[0].content


def _chunk_draft(rank: int, chunk_type: str, group: int, content: str):
    from zleap.sag.modules.load.chunking.types import ChunkDraft

    return ChunkDraft(
        rank=rank,
        heading="",
        content=content,
        raw_content=content,
        chunk_type="TEXT",
        section_order_indices=[rank],
        metadata={"chunk_type": chunk_type, "parent_group": group},
    )


@pytest_asyncio.fixture(scope="module")
async def zleap_runtime():
    """初始化 zleap-sag 运行时（建 schema），供入库回填/检索增强测试使用。"""
    from sag_api.core.config import settings
    from sag_api.sag.engine_manager import EngineManager

    manager = EngineManager(settings)
    await manager.provision("src_runtime", None)
    yield
    await manager.aclose_all()


async def _seed_base(session):
    """创建 SourceConfig 父记录，满足外键约束。"""
    from zleap.sag.db.models import SourceConfig

    await session.merge(SourceConfig(id="src_t", name="测试信源"))


async def _clear_chunks():
    from sqlalchemy import delete
    from zleap.sag.db import get_session_factory
    from zleap.sag.db.models import SourceChunk

    sf = get_session_factory()
    async with sf() as session:
        await session.execute(delete(SourceChunk))
        await _seed_base(session)
        await session.commit()


@pytest.mark.asyncio
async def test_backfill_parent_ids_writes_child_parent_id(zleap_runtime):
    from zleap.sag.db import get_session_factory
    from zleap.sag.db.models import SourceChunk
    from zleap.sag.modules.load.chunking.types import ChunkingResult

    from sag_api.sag.parent_child import _backfill_parent_ids

    await _clear_chunks()
    SessionLocal = get_session_factory()
    parent_id, child_id = str(uuid.uuid4()), str(uuid.uuid4())
    drafts = [
        _chunk_draft(0, "parent", 0, "父块内容"),
        _chunk_draft(1, "child", 0, "子块内容"),
    ]
    result = ChunkingResult(
        input_doc=None,
        blocks=[],
        article_sections=[],
        source_chunks=drafts,
    )

    async with SessionLocal() as session:
        await _seed_base(session)
        session.add(SourceChunk(id=parent_id, source_config_id="src_t", source_type="ARTICLE",
                                source_id="art_t", content="父块内容", rank=0, chunk_length=4))
        session.add(SourceChunk(id=child_id, source_config_id="src_t", source_type="ARTICLE",
                                source_id="art_t", content="子块内容", rank=1, chunk_length=4))
        await session.commit()

    await _backfill_parent_ids([parent_id, child_id], result)

    async with SessionLocal() as session:
        row = await session.get(SourceChunk, child_id)
    assert (row.extra_data or {}).get("parent_id") == parent_id


@pytest.mark.asyncio
async def test_enrich_parent_context_skips_legacy_data(zleap_runtime):
    """旧数据无 chunk_type/parent_id 标记 → 原样返回（增量安全）。"""
    from sag_api.sag.dto import RetrievedSection
    from sag_api.sag.parent_child import enrich_parent_context

    await _clear_chunks()
    section = RetrievedSection(chunk_id=str(uuid.uuid4()), heading="h", content="c", score=0.9)
    out = await enrich_parent_context([section])
    assert out == [section]


@pytest.mark.asyncio
async def test_enrich_parent_context_replaces_child_with_parent(zleap_runtime):
    from zleap.sag.db import get_session_factory
    from zleap.sag.db.models import SourceChunk

    from sag_api.sag.dto import RetrievedSection
    from sag_api.sag.parent_child import enrich_parent_context

    await _clear_chunks()
    SessionLocal = get_session_factory()
    parent_id, child_id = str(uuid.uuid4()), str(uuid.uuid4())
    async with SessionLocal() as session:
        await _seed_base(session)
        session.add(SourceChunk(
            id=parent_id, source_config_id="src_t", source_type="ARTICLE", source_id="art_t",
            content="父块完整上下文内容", heading="第一章", rank=0, chunk_length=8,
            extra_data={"chunk_type": "parent", "parent_group": 0},
        ))
        session.add(SourceChunk(
            id=child_id, source_config_id="src_t", source_type="ARTICLE", source_id="art_t",
            content="子块片段", rank=1, chunk_length=4,
            extra_data={"chunk_type": "child", "parent_group": 0, "parent_id": parent_id},
        ))
        await session.commit()

    child_section = RetrievedSection(chunk_id=child_id, heading="", content="子块片段", score=0.8)
    out = await enrich_parent_context([child_section])
    assert len(out) == 1
    assert out[0].content == "父块完整上下文内容"
    assert out[0].heading == "第一章"
    assert out[0].chunk_id == child_id  # 保留子块 id 用于溯源


@pytest.mark.asyncio
async def test_enrich_parent_context_deduplicates_parent_hit(zleap_runtime):
    """父块已作为独立命中 → 丢弃重复子块，保留父块完整结果。"""
    from zleap.sag.db import get_session_factory
    from zleap.sag.db.models import SourceChunk

    from sag_api.sag.dto import RetrievedSection
    from sag_api.sag.parent_child import enrich_parent_context

    await _clear_chunks()
    SessionLocal = get_session_factory()
    parent_id, child_id = str(uuid.uuid4()), str(uuid.uuid4())
    async with SessionLocal() as session:
        await _seed_base(session)
        session.add(SourceChunk(
            id=parent_id, source_config_id="src_t", source_type="ARTICLE", source_id="art_t",
            content="父块完整上下文内容", heading="第一章", rank=0, chunk_length=8,
            extra_data={"chunk_type": "parent", "parent_group": 0},
        ))
        session.add(SourceChunk(
            id=child_id, source_config_id="src_t", source_type="ARTICLE", source_id="art_t",
            content="子块片段", rank=1, chunk_length=4,
            extra_data={"chunk_type": "child", "parent_group": 0, "parent_id": parent_id},
        ))
        await session.commit()

    parent_section = RetrievedSection(chunk_id=parent_id, heading="第一章", content="父块完整上下文内容", score=0.85)
    child_section = RetrievedSection(chunk_id=child_id, heading="", content="子块片段", score=0.8)
    out = await enrich_parent_context([parent_section, child_section])
    assert [s.chunk_id for s in out] == [parent_id]


@pytest.mark.asyncio
async def test_real_loader_save_links_children_to_parents(zleap_runtime):
    """真实 DocumentLoader._save_to_database 链路：patch 生效并回填 parent_id。"""
    import uuid as _uuid

    from sqlalchemy import select
    from zleap.sag.db import get_session_factory
    from zleap.sag.db.models import Article, ArticleParseStatus, SourceChunk
    from zleap.sag.modules.load.loader import DocumentLoader
    from zleap.sag.modules.load.parser import MarkdownParser

    from sag_api.core.config import settings
    from sag_api.sag.chunking_compat import install_structural_chunking_patch
    from sag_api.sag.parent_child import install_parent_child_loader_patch

    await _clear_chunks()
    old_mode, old_max = settings.document_chunk_mode, settings.parent_chunk_max_tokens
    settings.document_chunk_mode = "parent_child"
    settings.parent_chunk_max_tokens = 512
    try:
        install_structural_chunking_patch()
        install_parent_child_loader_patch()
    finally:
        pass  # patch 保持安装到断言结束

    scid = "src_e2e"
    sf = get_session_factory()
    async with sf() as s:
        from zleap.sag.db.models import SourceConfig

        await s.merge(SourceConfig(id=scid, name="e2e"))
        article_id = _uuid.uuid4().hex
        s.add(Article(id=article_id, source_config_id=scid, source_id=article_id,
                      title="t", content="# t\n\n第一段。\n\n第二段。", status="COMPLETED",
                      parse_status=ArticleParseStatus.COMPLETED))
        await s.commit()

    md = "# 标题\n\n第一段文字，包含一些内容。\n\n第二段文字。\n\n```python\nprint(1)\n```\n"
    rp = MarkdownParser(max_tokens=200, chunk_mode="standard")
    result = await rp.parse_content_with_plan_async(md)
    children = [c for c in result.source_chunks if (c.metadata or {}).get("chunk_type") == "child"]

    loader = DocumentLoader()
    await loader._save_to_database("t", md, scid, article_id, result, document_id_for_binding=article_id)

    async with sf() as s:
        rows = (await s.execute(select(SourceChunk).where(SourceChunk.source_id == article_id))).scalars().all()
    linked = 0
    for row in rows:
        if (row.extra_data or {}).get("chunk_type") == "child":
            assert (row.extra_data or {}).get("parent_id"), f"child {row.id} 缺 parent_id"
            linked += 1
    assert linked == len(children)

    settings.document_chunk_mode = old_mode
    settings.parent_chunk_max_tokens = old_max
