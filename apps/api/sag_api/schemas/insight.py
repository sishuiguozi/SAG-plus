from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class EntityOut(BaseModel):
    id: str
    name: str
    type: str
    description: str
    heat: int


class GraphDocumentOut(BaseModel):
    id: str
    filename: str
    status: str
    chunk_count: int
    event_count: int
    created_at: datetime


class GraphEventOut(BaseModel):
    id: str
    document_id: str | None = None
    title: str
    summary: str = ""
    category: str = ""
    rank: int = 0
    parent_id: str | None = None
    chunk_id: str | None = None
    start_time: datetime | None = None
    # SAG-OPT-603：该事件在知识库中的真实实体关联数（供前端区分“无实体关系”与“因预算未加载”）
    relation_count: int = 0


GraphNodeKind = Literal["document", "event", "entity"]
GraphRelationKind = Literal["contains", "subevent", "mentions"]


class GraphRelationOut(BaseModel):
    source_id: str
    source_kind: GraphNodeKind
    target_id: str
    target_kind: GraphNodeKind
    kind: GraphRelationKind
    weight: float = 1.0
    description: str = ""


class GraphCountsOut(BaseModel):
    documents: int
    events: int
    entities: int
    shown_documents: int
    shown_events: int
    shown_entities: int
    shown_relations: int


class SourceGraphOut(BaseModel):
    documents: list[GraphDocumentOut]
    events: list[GraphEventOut]
    entities: list[EntityOut]
    relations: list[GraphRelationOut]
    counts: GraphCountsOut
    truncated: bool
