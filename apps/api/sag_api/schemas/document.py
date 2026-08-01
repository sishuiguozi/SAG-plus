from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from sag_api.enums import DocumentStatus


class DocumentActivityItem(BaseModel):
    """SAG-OPT-502：单个文档最近状态变化的快照（旧状态由前端轮询快照对比推导）。"""

    document_id: str
    filename: str
    status: DocumentStatus
    progress: int = 0
    error: str | None = None
    updated_at: datetime | None = None


class DocumentActivityResponse(BaseModel):
    events: list[DocumentActivityItem]



class MessageItem(BaseModel):
    text: str
    author: str | None = None
    role: str | None = None
    ts: str | None = None
    thread: str | None = None


class IngestRequest(BaseModel):
    """统一写入：文本或一批消息，二选一。"""

    text: str | None = None
    title: str | None = None
    messages: list[MessageItem] | None = Field(default=None)


class DocumentDeleteRequest(BaseModel):
    """删除文档需要账户密码确认。"""

    password: str = Field(min_length=1, max_length=200)


class RenameRequest(BaseModel):
    """重命名文档显示名。"""

    filename: str = Field(min_length=1, max_length=512)


class ChunkOut(BaseModel):
    """单个分块的浏览视图（只读）。"""

    chunk_id: str
    heading: str
    content: str
    rank: int
    chunk_length: int


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_id: str
    filename: str
    content_type: str
    size_bytes: int
    status: DocumentStatus
    chunk_count: int
    event_count: int
    progress: int
    token_usage: int
    error: str | None
    created_at: datetime
    updated_at: datetime

    @field_validator("error", mode="before")
    @classmethod
    def redact_storage_error(cls, value: object) -> object:
        """Keep SQL and local storage details in server logs, not in the UI."""
        if not isinstance(value, str):
            return value
        normalized = value.lower()
        sql_markers = (
            "sqlite3.",
            "sqlalchemy.exc",
            "[sql:",
            "[parameters:",
            "sqlalche.me/e/",
        )
        if not any(marker in normalized for marker in sql_markers):
            return value
        if "foreign key constraint failed" in normalized:
            return "信息源初始化未完成，文档尚未入库，请重试。"
        return "文档入库失败，请重试；若仍失败，请查看服务日志。"
