"""ORM 模型聚合导入 —— 保证 Base.metadata 注册全部表。"""

from sag_api.db.models.agent import Agent, AgentBinding, Message, Thread
from sag_api.db.models.document import Document
from sag_api.db.models.job import Job
from sag_api.db.models.maintenance import MaintenanceLease
from sag_api.db.models.setting import Setting
from sag_api.db.models.source import Source
from sag_api.db.models.source_graph_cache import SourceGraphCache
from sag_api.db.models.universe import (
    ExplorationSession,
    ExplorationStep,
    UniverseDirtySource,
    UniverseOverview,
    UniversePartition,
)
from sag_api.db.models.universe_graph_cache import UniverseGraphCache
from sag_api.db.models.user import User
from sag_api.db.models.vector_write import VectorWriteItem, VectorWriteJob

__all__ = [
    "Agent",
    "AgentBinding",
    "Document",
    "Job",
    "MaintenanceLease",
    "Message",
    "Setting",
    "Source",
    "SourceGraphCache",
    "Thread",
    "User",
    "ExplorationSession",
    "ExplorationStep",
    "UniverseDirtySource",
    "UniverseGraphCache",
    "UniverseOverview",
    "UniversePartition",
    "VectorWriteItem",
    "VectorWriteJob",
]
