from __future__ import annotations

import uuid
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_real_code_loader_persists_metadata_and_parent_links(tmp_path: Path):
    from sqlalchemy import select
    from zleap.sag.db import get_session_factory
    from zleap.sag.db.models import Article, SourceChunk
    from zleap.sag.modules.load.config import DocumentLoadConfig

    from sag_api.code_ingest.loader import CodeDocumentLoader, PrecomputedCodeParser
    from sag_api.core.config import settings
    from sag_api.sag.engine_manager import EngineManager
    from sag_api.sag.parent_child import install_parent_child_loader_patch
    from tests.test_code_document_loader import _parsed_document

    manager = EngineManager(settings)
    source_config_id = f"src_code_{uuid.uuid4().hex[:12]}"
    await manager.provision(source_config_id, None)
    install_parent_child_loader_patch()
    old_vectorize = settings.parent_chunk_vectorize
    settings.parent_chunk_vectorize = False
    try:
        source = "class Service:\n    def run(self):\n        return 1\n"
        path = tmp_path / "service.py"
        path.write_text(source, encoding="utf-8")
        loader = CodeDocumentLoader(PrecomputedCodeParser(_parsed_document(source)))
        loaded = await loader.load(
            DocumentLoadConfig(
                path=path,
                source_config_id=source_config_id,
                auto_vector=False,
            )
        )

        session_factory = get_session_factory()
        async with session_factory() as session:
            article = await session.get(Article, loaded.source_id)
            rows = list(
                (
                    await session.execute(
                        select(SourceChunk)
                        .where(SourceChunk.source_id == loaded.source_id)
                        .order_by(SourceChunk.rank)
                    )
                )
                .scalars()
                .all()
            )

        assert article is not None
        assert article.title == "repo/service.py"
        assert len(rows) == 3
        assert [row.extra_data["chunk_type"] for row in rows] == [
            "code_parent",
            "code_parent",
            "code_child",
        ]
        assert all(row.extra_data["content_sha256"] == "f" * 64 for row in rows)
        class_parent = next(row for row in rows if row.extra_data["symbol_kind"] == "class")
        method_child = next(row for row in rows if row.extra_data["symbol_kind"] == "method")
        assert method_child.extra_data["parent_id"] == class_parent.id
        # parent_chunk_vectorize is enforced by the batch index filter; with
        # auto_vector=False we still ensure parent/child metadata is intact.
        assert class_parent.extra_data["chunk_type"] == "code_parent"
        assert method_child.extra_data["chunk_type"] == "code_child"
    finally:
        settings.parent_chunk_vectorize = old_vectorize
        await manager.aclose_all()


@pytest.mark.asyncio
async def test_code_parent_context_enrichment_uses_code_chunk_types(tmp_path: Path):
    from zleap.sag.db import get_session_factory
    from zleap.sag.db.models import SourceChunk, SourceConfig

    from sag_api.core.config import settings
    from sag_api.sag.dto import RetrievedSection
    from sag_api.sag.engine_manager import EngineManager
    from sag_api.sag.parent_child import enrich_parent_context

    manager = EngineManager(settings)
    source_config_id = f"src_code_{uuid.uuid4().hex[:12]}"
    await manager.provision(source_config_id, None)
    try:
        session_factory = get_session_factory()
        parent_id, child_id = str(uuid.uuid4()), str(uuid.uuid4())
        async with session_factory() as session:
            await session.merge(SourceConfig(id=source_config_id, name="code"))
            session.add(
                SourceChunk(
                    id=parent_id,
                    source_config_id=source_config_id,
                    source_type="ARTICLE",
                    source_id="art_code",
                    content="class Service:\n    ...",
                    heading="Service",
                    rank=0,
                    chunk_length=10,
                    extra_data={"chunk_type": "code_parent", "parent_group": 0},
                )
            )
            session.add(
                SourceChunk(
                    id=child_id,
                    source_config_id=source_config_id,
                    source_type="ARTICLE",
                    source_id="art_code",
                    content="def run(self):\n    return 1",
                    rank=1,
                    chunk_length=10,
                    extra_data={
                        "chunk_type": "code_child",
                        "parent_group": 0,
                        "parent_id": parent_id,
                    },
                )
            )
            await session.commit()

        out = await enrich_parent_context(
            [RetrievedSection(chunk_id=child_id, heading="", content="def run", score=0.9)]
        )
        assert len(out) == 1
        assert out[0].content == "class Service:\n    ..."
        assert out[0].heading == "Service"
        assert out[0].chunk_id == child_id
    finally:
        await manager.aclose_all()
