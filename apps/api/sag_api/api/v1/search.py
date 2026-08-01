from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypedDict

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from sag_api.core.config import settings
from sag_api.core.db import get_session
from sag_api.core.deps import get_current_user, get_engine_manager, get_llm
from sag_api.core.errors import ApiError
from sag_api.core.logging import get_logger
from sag_api.db.models import Source, User
from sag_api.generation import LLMClient
from sag_api.sag import EngineManager, RetrievedSection, SearchOutcome
from sag_api.sag.vector_write_items import (
    aggregate_aux_index_status,
    aux_index_backfill_status,
)
from sag_api.schemas.insight import EntityOut, GraphRelationOut
from sag_api.schemas.search import (
    GlobalSearchRequest,
    SearchEventOut,
    SearchRequest,
    SearchResponse,
    SearchSourceHitOut,
    SectionOut,
)
from sag_api.services.retrieval_service import (
    EventScoreMap,
    recall_event_scores,
    retrieve_relevant_sections,
    stream_synthesize_search_answer,
    synthesize_search_answer,
)
from sag_api.services.source_service import get_source, search_source_candidates

router = APIRouter(prefix="/sources/{source_id}/search", tags=["search"])
global_router = APIRouter(prefix="/search", tags=["search"])
log = get_logger("search")


class RelatedQuestionsRequest(BaseModel):
    query: str
    source_ids: list[str] | None = None


class RelatedQuestionsResponse(BaseModel):
    questions: list[str]


@global_router.post("/related", response_model=RelatedQuestionsResponse)
async def related_questions(
    body: RelatedQuestionsRequest,
    _user: User = Depends(get_current_user),
    llm: LLMClient = Depends(get_llm),
) -> RelatedQuestionsResponse:
    """根据查询生成相关追问，供搜索探索引导。LLM 未配置或失败时静默返回空列表。"""
    query = (body.query or "").strip()
    if not query:
        return RelatedQuestionsResponse(questions=[])
    prompt = (
        "根据用户的查询，生成 3 到 5 条有助于深入探索的相关问题。"
        "每行一条，不要编号、不要项目符号、不要额外解释，语言与查询保持一致。\n\n"
        f"查询：{query}"
    )
    try:
        raw = await llm.complete(
            [
                {"role": "system", "content": "你只输出相关问题列表，每行一条。"},
                {"role": "user", "content": prompt},
            ]
        )
    except Exception:  # noqa: BLE001 - 相关追问是锦上添花，失败静默返回空
        return RelatedQuestionsResponse(questions=[])
    questions = [
        # 按字符集剥离序号前缀，属预期行为（B005）
        line.strip().lstrip("0123456789.-、) .").strip()  # noqa: B005
        for line in (raw or "").splitlines()
        if line.strip()
    ]
    return RelatedQuestionsResponse(questions=questions[:5])


class _EventGraphFields(TypedDict):
    events: list[SearchEventOut]
    entities: list[EntityOut]
    relations: list[GraphRelationOut]


def _source_hits(events: list[SearchEventOut]) -> list[SearchSourceHitOut]:
    def utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    grouped: dict[str, dict] = {}
    seen: set[tuple[str, str]] = set()
    for event in events:
        if not event.source_id:
            continue
        key = (event.source_id, event.id)
        if key in seen:
            continue
        seen.add(key)
        item = grouped.setdefault(
            event.source_id,
            {
                "source_id": event.source_id,
                "source_name": event.source_name,
                "event_hits": 0,
                "max_score": 0.0,
                "latest_event_time": None,
            },
        )
        item["event_hits"] += 1
        item["max_score"] = max(float(item["max_score"]), float(event.score or 0.0))
        if event.start_time is not None:
            event_time = utc(event.start_time)
            if item["latest_event_time"] is None or event_time > item["latest_event_time"]:
                item["latest_event_time"] = event_time
    ranked = sorted(
        grouped.values(),
        key=lambda item: (
            -int(item["event_hits"]),
            -float(item["max_score"]),
            -(item["latest_event_time"].timestamp() if item["latest_event_time"] else 0.0),
            str(item["source_id"]),
        ),
    )
    return [SearchSourceHitOut(**item) for item in ranked]


async def _event_graph_fields(
    engine_manager: EngineManager,
    sections: list[RetrievedSection],
    sources_by_config: dict[str, Source],
    *,
    event_scores: EventScoreMap | None = None,
) -> _EventGraphFields:
    event_scores = event_scores or {}
    if not sections and not event_scores:
        return {"events": [], "entities": [], "relations": []}
    graph = await engine_manager.graph_for_sections(
        sections,
        sources_by_config,
        event_limit=max(1, len(sections), len(event_scores)),
        event_scores=event_scores,
    )
    events = []
    for event in graph.events:
        source = sources_by_config.get(event.source_config_id)
        events.append(
            SearchEventOut(
                id=event.id,
                source_id=source.id if source else None,
                source_name=source.name if source else None,
                title=event.title,
                summary=event.summary,
                category=event.category,
                rank=event.rank,
                parent_id=event.parent_id,
                chunk_id=event.chunk_id,
                start_time=event.start_time,
                score=event.score,
            )
        )
    return {
        "events": events,
        "entities": [EntityOut(**entity.model_dump()) for entity in graph.entities],
        "relations": [
            GraphRelationOut(
                source_id=association.event_id,
                source_kind="event",
                target_id=association.entity_id,
                target_kind="entity",
                kind="mentions",
                weight=association.weight,
                description=association.description,
            )
            for association in graph.associations
        ],
    }


@dataclass(slots=True)
class _PreparedGlobalSearch:
    sources: list[Source]
    outcome: SearchOutcome
    response: SearchResponse


async def _prepare_global_search(
    session: AsyncSession,
    engine_manager: EngineManager,
    body: GlobalSearchRequest,
    llm: Any | None = None,
) -> _PreparedGlobalSearch:
    sources = await search_source_candidates(session, body.source_ids)
    # 检索命中信源的辅助向量索引补齐信号（SAG-OPT-107）：核心向量为 P0 必检，
    # 辅助向量（entity/event-entity）为 P1，缺失/补齐中时在前端明确标注。
    backfill_per_source = await aux_index_backfill_status(
        session,
        [source.sag_source_config_id for source in sources],
        aux_vector_deferred_enabled=settings.aux_vector_deferred_enabled,
    )
    aux_index = aggregate_aux_index_status(backfill_per_source)
    # Retrieval and answer generation can be long-running. End the read-only
    # transaction as soon as source identity has been materialized so an SSE
    # request never occupies a pooled database connection while waiting on the
    # engine or model. SessionLocal uses expire_on_commit=False.
    await session.commit()
    if not sources:
        outcome = SearchOutcome(query=body.query, sections=[], stats={"sources": 0})
        return _PreparedGlobalSearch(
            sources=[],
            outcome=outcome,
            response=SearchResponse(query=body.query, sections=[], stats=outcome.stats),
        )

    refs = {source.sag_source_config_id: source for source in sources}
    outcome, event_scores = await asyncio.gather(
        retrieve_relevant_sections(
            engine_manager,
            sources,
            body.query,
            strategy=body.strategy,
            top_k=body.top_k,
            llm=llm,
        ),
        recall_event_scores(
            engine_manager,
            body.query,
            refs,
            limit=body.top_k,
        ),
    )
    graph_fields = await _event_graph_fields(
        engine_manager,
        outcome.sections,
        refs,
        event_scores=event_scores,
    )
    stats = {
        **outcome.stats,
        "event_candidates": len(event_scores),
        "event_hits": len(graph_fields["events"]),
        "event_recall": "vector+chunk" if event_scores else "chunk",
    }

    section_outputs = []
    for section in outcome.sections:
        source = refs.get(section.source_config_id or "")
        section_outputs.append(
            SectionOut(
                **{
                    **section.model_dump(),
                    "source_id": source.id if source else None,
                },
                source_name=source.name if source else None,
            )
        )

    return _PreparedGlobalSearch(
        sources=sources,
        outcome=outcome,
        response=SearchResponse(
            query=outcome.query,
            sections=section_outputs,
            **graph_fields,
            source_hits=_source_hits(graph_fields["events"]),
            aux_index=aux_index,
            stats=stats,
        ),
    )


async def _complete_global_search(
    session: AsyncSession,
    user: User,
    body: GlobalSearchRequest,
    prepared: _PreparedGlobalSearch,
    summary: str,
) -> SearchResponse:
    exploration_id = None
    if body.save_exploration and prepared.sources:
        from sag_api.services.universe_service import save_exploration

        response = prepared.response
        section_refs = [
            {
                "n": index,
                "chunk_id": item.chunk_id,
                "heading": item.heading,
                "score": item.score,
                "source_id": item.source_id,
                "source_name": item.source_name,
            }
            for index, item in enumerate(response.sections, 1)
        ]
        exploration, _step = await save_exploration(
            session,
            user_id=user.id,
            query=prepared.outcome.query,
            source_ids=[source.id for source in prepared.sources],
            summary=summary,
            events=[item.model_dump(mode="json") for item in response.events],
            entities=[item.model_dump(mode="json") for item in response.entities],
            relations=[item.model_dump(mode="json") for item in response.relations],
            evidence=section_refs,
        )
        exploration_id = exploration.id

    return prepared.response.model_copy(update={"summary": summary, "exploration_id": exploration_id})


def _sse(event: str, payload: dict) -> dict[str, str]:
    return {"event": event, "data": json.dumps(payload, ensure_ascii=False)}


@router.post("", response_model=SearchResponse)
async def search(
    source_id: str,
    body: SearchRequest,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    engine_manager: EngineManager = Depends(get_engine_manager),
    llm: LLMClient = Depends(get_llm),
) -> SearchResponse:
    source = await get_source(session, source_id)
    refs = {source.sag_source_config_id: source}
    backfill_per_source = await aux_index_backfill_status(
        session,
        [source.sag_source_config_id],
        aux_vector_deferred_enabled=settings.aux_vector_deferred_enabled,
    )
    aux_index = aggregate_aux_index_status(backfill_per_source)
    outcome, event_scores = await asyncio.gather(
        retrieve_relevant_sections(
            engine_manager,
            [source],
            body.query,
            strategy=body.strategy,
            top_k=body.top_k,
            llm=llm,
        ),
        recall_event_scores(
            engine_manager,
            body.query,
            refs,
            limit=body.top_k,
        ),
    )
    for section in outcome.sections:
        section.source_config_id = section.source_config_id or source.sag_source_config_id
    graph_fields = await _event_graph_fields(
        engine_manager,
        outcome.sections,
        refs,
        event_scores=event_scores,
    )
    # 对外 source_id = sag 信源 id（可路由 / 取原文），不泄漏引擎内部 id
    return SearchResponse(
        query=outcome.query,
        sections=[
            SectionOut(**{**s.model_dump(), "source_id": source.id}, source_name=source.name) for s in outcome.sections
        ],
        **graph_fields,
        source_hits=_source_hits(graph_fields["events"]),
        aux_index=aux_index,
        summary=await synthesize_search_answer(
            outcome.query,
            outcome.sections,
            llm=llm,
        ),
        stats={
            **outcome.stats,
            "event_candidates": len(event_scores),
            "event_hits": len(graph_fields["events"]),
            "event_recall": "vector+chunk" if event_scores else "chunk",
        },
    )


@global_router.post("", response_model=SearchResponse)
async def global_search(
    body: GlobalSearchRequest,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    engine_manager: EngineManager = Depends(get_engine_manager),
    llm: LLMClient = Depends(get_llm),
) -> SearchResponse:
    """全局搜索：先选有界信源分区，再 fan-out 检索并返回可追溯结果。"""
    prepared = await _prepare_global_search(session, engine_manager, body, llm=llm)
    summary = await synthesize_search_answer(
        prepared.outcome.query,
        prepared.outcome.sections,
        llm=llm,
    )
    return await _complete_global_search(
        session,
        _user,
        body,
        prepared,
        summary,
    )


@global_router.post("/stream")
async def global_search_stream(
    body: GlobalSearchRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    engine_manager: EngineManager = Depends(get_engine_manager),
    llm: LLMClient = Depends(get_llm),
) -> EventSourceResponse:
    """Stream a grounded summary after returning the stable retrieval result."""

    async def event_gen():
        try:
            # Run retrieval inside the response task: EventSourceResponse can
            # send keep-alive pings immediately and cancel this work as soon as
            # the browser starts a newer search or disconnects.
            prepared = await _prepare_global_search(session, engine_manager, body)
            yield _sse("result", prepared.response.model_dump(mode="json"))
            summary = ""
            async for update in stream_synthesize_search_answer(
                prepared.outcome.query,
                prepared.outcome.sections,
                llm=llm,
            ):
                if update.kind == "delta":
                    yield _sse("summary.delta", {"delta": update.text})
                else:
                    summary = update.text

            completed = await _complete_global_search(
                session,
                user,
                body,
                prepared,
                summary,
            )
            yield _sse("completed", completed.model_dump(mode="json"))
        except asyncio.CancelledError:
            # Client disconnect/new search cancellation must stop the upstream
            # model stream, not be reported as a failed search.
            raise
        except ApiError as error:
            log.warning("搜索流异常终止：%s", error.message)
            yield _sse("error", {"code": error.code, "message": error.message})
        except Exception as error:  # noqa: BLE001
            log.exception("搜索流未处理异常：%s", error)
            yield _sse(
                "error",
                {"code": "stream_error", "message": "搜索生成意外中断"},
            )

    return EventSourceResponse(
        event_gen(),
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
