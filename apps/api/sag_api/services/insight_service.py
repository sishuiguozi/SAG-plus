"""洞察领域逻辑：事件—实体图谱读取（供未来图谱视图与 get_entity 工具）。"""

from __future__ import annotations

from collections import OrderedDict
from datetime import UTC, datetime

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.config import settings
from sag_api.core.errors import ServiceUnavailableError
from sag_api.db.models import Document, Job, Source, SourceGraphCache
from sag_api.enums import DocumentStatus, JobStatus, JobType
from sag_api.sag import EngineManager, EntityInfo
from sag_api.schemas.insight import (
    EntityOut,
    GraphCountsOut,
    GraphDocumentOut,
    GraphEventOut,
    GraphRelationOut,
    SourceGraphOut,
)


async def list_entities(
    engine_manager: EngineManager, source: Source, *, types: list[str] | None = None, limit: int = 100
) -> list[EntityInfo]:
    return await engine_manager.list_entities(source.sag_source_config_id, source=source, types=types, limit=limit)


_GRAPH_CACHE_SCHEMA_VERSION = 2
_GRAPH_MEMORY_CACHE_LIMIT = 24
_graph_memory_cache: OrderedDict[tuple[str, str, str], SourceGraphOut] = OrderedDict()


def _load_graph_memory_cache(
    source_id: str,
    cache_key: str,
    revision: str | None,
) -> SourceGraphOut | None:
    if revision is None:
        return None
    key = (source_id, cache_key, revision)
    graph = _graph_memory_cache.get(key)
    if graph is not None:
        _graph_memory_cache.move_to_end(key)
    return graph


def _save_graph_memory_cache(
    source_id: str,
    cache_key: str,
    revision: str,
    graph: SourceGraphOut,
) -> None:
    key = (source_id, cache_key, revision)
    _graph_memory_cache[key] = graph
    _graph_memory_cache.move_to_end(key)
    while len(_graph_memory_cache) > _GRAPH_MEMORY_CACHE_LIMIT:
        _graph_memory_cache.popitem(last=False)


def _graph_cache_key(*, document_limit: int, event_limit: int, entity_limit: int) -> str:
    return (
        f"default:v{_GRAPH_CACHE_SCHEMA_VERSION}:"
        f"d{document_limit}:e{event_limit}:n{entity_limit}"
    )


async def _source_graph_revision(session: AsyncSession, source: Source) -> str:
    latest_document_update = (
        await session.execute(
            select(func.max(Document.updated_at)).where(Document.source_id == source.id)
        )
    ).scalar_one_or_none()
    latest = latest_document_update.isoformat() if latest_document_update else "-"
    return "|".join(
        [
            source.sag_source_config_id,
            source.updated_at.isoformat(),
            latest,
            str(int(source.document_count or 0)),
            str(int(source.chunk_count or 0)),
            str(int(source.event_count or 0)),
        ]
    )


async def _graph_build_is_busy(session: AsyncSession, source: Source) -> bool:
    active_documents = int(
        (
            await session.execute(
                select(func.count(Document.id)).where(
                    Document.source_id == source.id,
                    Document.status.in_(
                        [
                            DocumentStatus.PENDING,
                            DocumentStatus.LOADING,
                            DocumentStatus.EXTRACTING,
                        ]
                    ),
                )
            )
        ).scalar_one()
        or 0
    )
    if active_documents:
        return True

    active_jobs = int(
        (
            await session.execute(
                select(func.count(Job.id)).join(
                    Document,
                    Document.id == Job.document_id,
                ).where(
                    Job.type == JobType.PROCESS_DOCUMENT,
                    Job.source_id == source.id,
                    Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
                    Document.status.in_(
                        [
                            DocumentStatus.PENDING,
                            DocumentStatus.LOADING,
                            DocumentStatus.EXTRACTING,
                        ]
                    ),
                )
            )
        ).scalar_one()
        or 0
    )
    if active_jobs:
        return True
    return False


async def _load_graph_cache(
    session: AsyncSession,
    source: Source,
    *,
    cache_key: str,
    revision: str | None = None,
) -> SourceGraphOut | None:
    memory_cached = _load_graph_memory_cache(source.id, cache_key, revision)
    if memory_cached is not None:
        return memory_cached
    conditions = [
        SourceGraphCache.source_id == source.id,
        SourceGraphCache.cache_key == cache_key,
    ]
    if revision is not None:
        conditions.append(SourceGraphCache.revision == revision)
    cache = await session.scalar(
        select(SourceGraphCache)
        .where(*conditions)
        .order_by(SourceGraphCache.updated_at.desc(), SourceGraphCache.id.desc())
        .limit(1)
    )
    if cache is None:
        return None
    try:
        graph = SourceGraphOut.model_validate(cache.payload)
        if revision is not None:
            _save_graph_memory_cache(source.id, cache_key, revision, graph)
        return graph
    except Exception:
        return None


async def _save_graph_cache(
    session: AsyncSession,
    source: Source,
    *,
    cache_key: str,
    revision: str,
    document_limit: int,
    event_limit: int,
    entity_limit: int,
    graph: SourceGraphOut,
) -> None:
    cache = await session.scalar(
        select(SourceGraphCache).where(
            SourceGraphCache.source_id == source.id,
            SourceGraphCache.cache_key == cache_key,
        )
    )
    payload = graph.model_dump(mode="json")
    if cache is None:
        session.add(
            SourceGraphCache(
                source_id=source.id,
                source_config_id=source.sag_source_config_id,
                cache_key=cache_key,
                revision=revision,
                document_limit=document_limit,
                event_limit=event_limit,
                entity_limit=entity_limit,
                payload=payload,
                built_at=datetime.now(UTC),
            )
        )
    else:
        cache.source_config_id = source.sag_source_config_id
        cache.revision = revision
        cache.document_limit = document_limit
        cache.event_limit = event_limit
        cache.entity_limit = entity_limit
        cache.payload = payload
        cache.built_at = datetime.now(UTC)
        cache.error = None
    await session.commit()
    _save_graph_memory_cache(source.id, cache_key, revision, graph)


def _event_relation_counts(event_ids: list[str]) -> dict[str, int]:
    """批量查询事件在引擎库中的真实实体关联数（只读；失败降级为空）。"""
    if not event_ids:
        return {}
    from pathlib import Path as _Path

    path = _Path(settings.data_dir) / "sag.db"
    if not path.exists():
        return {}
    import sqlite3

    try:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=15)
        try:
            placeholders = ",".join("?" * len(event_ids))
            rows = con.execute(
                "SELECT event_id, count(*) FROM event_entity "
                f"WHERE event_id IN ({placeholders}) AND (is_delete IS NULL OR is_delete = 0) "
                "GROUP BY event_id",
                event_ids,
            ).fetchall()
            return {str(row[0]): int(row[1]) for row in rows}
        finally:
            con.close()
    except Exception:  # noqa: BLE001 - 关系计数失败不影响图谱主流程
        return {}


async def get_source_graph(
    session: AsyncSession,
    engine_manager: EngineManager,
    source: Source,
    *,
    document_limit: int = 1_000,
    event_limit: int = 1_000,
    entity_limit: int = 1_000,
    document_ids: list[str] | None = None,
) -> SourceGraphOut:
    """拼装 Web 文档与引擎事件/实体，按调用方给出的性能预算返回图谱。"""
    scoped_document_ids = (
        list(dict.fromkeys(document_id.strip() for document_id in document_ids if document_id.strip()))
        if document_ids is not None
        else None
    )
    cache_key: str | None = None
    revision: str | None = None
    if scoped_document_ids is None:
        cache_key = _graph_cache_key(
            document_limit=document_limit,
            event_limit=event_limit,
            entity_limit=entity_limit,
        )
        revision = await _source_graph_revision(session, source)
        cached = await _load_graph_cache(
            session,
            source,
            cache_key=cache_key,
            revision=revision,
        )
        if cached is not None:
            return cached
        if await _graph_build_is_busy(session, source):
            stale = await _load_graph_cache(session, source, cache_key=cache_key)
            if stale is not None:
                return stale
            raise ServiceUnavailableError("入库仍在进行，暂不构建图谱缓存，避免挤占入库资源。")
    elif await _graph_build_is_busy(session, source):
        raise ServiceUnavailableError("入库仍在进行，暂不实时计算图谱，避免挤占入库资源。")

    document_scope = [Document.source_id == source.id]
    if scoped_document_ids is not None:
        document_scope.append(Document.id.in_(scoped_document_ids))
    else:
        # The source graph is a visual slice, not a document activity feed. When
        # a large import is in progress, the newest documents are often still
        # PENDING/LOADING and have no engine source id/events yet; sampling by
        # created_at alone makes the 2D/3D graph look empty even though older
        # READY documents already have a rich event-entity graph.
        document_scope.extend(
            [
                Document.sag_source_id.is_not(None),
                Document.event_count > 0,
            ]
        )

    scoped_document_count, scoped_event_count = (
        await session.execute(
            select(
                func.count(Document.id),
                func.coalesce(func.sum(Document.event_count), 0),
            ).where(*document_scope)
        )
    ).one()
    scoped_document_count = int(scoped_document_count or 0)
    scoped_event_count = int(scoped_event_count or 0)
    documents = list(
        (
            await session.execute(
                select(Document)
                .where(*document_scope)
                .order_by(
                    case(
                        (Document.status == DocumentStatus.READY, 0),
                        (Document.status == DocumentStatus.EXTRACTING, 1),
                        (Document.status == DocumentStatus.FAILED, 2),
                        (Document.status == DocumentStatus.PAUSED, 3),
                        (Document.status == DocumentStatus.LOADING, 4),
                        (Document.status == DocumentStatus.PENDING, 5),
                        else_=6,
                    ),
                    Document.event_count.desc(),
                    Document.created_at.desc(),
                    Document.id.desc(),
                )
                .limit(document_limit)
            )
        )
        .scalars()
        .all()
    )
    source_id_to_document_id = {document.sag_source_id: document.id for document in documents if document.sag_source_id}
    shown_document_event_count = sum(max(0, int(document.event_count or 0)) for document in documents)
    graph = await engine_manager.source_graph(
        source.sag_source_config_id,
        list(source_id_to_document_id),
        source=source,
        event_limit=event_limit,
        entity_limit=entity_limit,
        expected_event_count=shown_document_event_count,
    )

    document_nodes = [
        GraphDocumentOut(
            id=document.id,
            filename=document.filename,
            status=document.status.value,
            chunk_count=document.chunk_count,
            event_count=document.event_count,
            created_at=document.created_at,
        )
        for document in documents
    ]
    event_relation_counts = _event_relation_counts([event.id for event in graph.events])
    event_nodes = [
        GraphEventOut(
            id=event.id,
            document_id=source_id_to_document_id.get(event.source_id),
            title=event.title,
            summary=event.summary,
            category=event.category,
            rank=event.rank,
            parent_id=event.parent_id,
            chunk_id=event.chunk_id,
            start_time=event.start_time,
            relation_count=event_relation_counts.get(event.id, 0),
        )
        for event in graph.events
    ]
    entity_nodes = [EntityOut(**entity.model_dump()) for entity in graph.entities]

    selected_event_ids = {event.id for event in event_nodes}
    relations: list[GraphRelationOut] = []
    relation_keys: set[tuple[str, str, str]] = set()

    def add_relation(relation: GraphRelationOut) -> None:
        key = (relation.source_id, relation.target_id, relation.kind)
        if key not in relation_keys:
            relations.append(relation)
            relation_keys.add(key)

    for event in event_nodes:
        if event.parent_id and event.parent_id in selected_event_ids:
            add_relation(
                GraphRelationOut(
                    source_id=event.parent_id,
                    source_kind="event",
                    target_id=event.id,
                    target_kind="event",
                    kind="subevent",
                )
            )
        elif event.document_id:
            add_relation(
                GraphRelationOut(
                    source_id=event.document_id,
                    source_kind="document",
                    target_id=event.id,
                    target_kind="event",
                    kind="contains",
                )
            )
    for association in graph.associations:
        add_relation(
            GraphRelationOut(
                source_id=association.event_id,
                source_kind="event",
                target_id=association.entity_id,
                target_kind="entity",
                kind="mentions",
                weight=association.weight,
                description=association.description,
            )
        )

    counts = GraphCountsOut(
        documents=(
            scoped_document_count
            if scoped_document_ids is not None
            else max(source.document_count, scoped_document_count, len(document_nodes))
        ),
        # Document checkpoints advance while extraction is still running;
        # Source.event_count is committed only after the whole document. Use
        # the strongest available total so a live graph reports 3 / 73 rather
        # than claiming its current three-node slice is the entire dataset.
        events=(
            max(scoped_event_count, len(event_nodes))
            if scoped_document_ids is not None
            else max(source.event_count, scoped_event_count, len(event_nodes))
        ),
        entities=max(graph.total_entities, len(entity_nodes)),
        shown_documents=len(document_nodes),
        shown_events=len(event_nodes),
        shown_entities=len(entity_nodes),
        shown_relations=len(relations),
    )
    response = SourceGraphOut(
        documents=document_nodes,
        events=event_nodes,
        entities=entity_nodes,
        relations=relations,
        counts=counts,
        truncated=(
            counts.documents > counts.shown_documents
            or counts.events > counts.shown_events
            or counts.entities > counts.shown_entities
        ),
    )
    if cache_key is not None and revision is not None:
        await _save_graph_cache(
            session,
            source,
            cache_key=cache_key,
            revision=revision,
            document_limit=document_limit,
            event_limit=event_limit,
            entity_limit=entity_limit,
            graph=response,
        )
    return response
