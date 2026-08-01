"""任务队列抽象。

MVP 用进程内 asyncio 队列（`InProcessAsyncQueue`）；接口保持精简，
未来可实现 Celery / RQ / Arq 等分布式后端而不影响调用方。
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class JobQueue(ABC):
    @abstractmethod
    async def enqueue(self, job_id: str) -> None:
        """把一个已持久化的 Job 投入队列等待执行。"""

    async def start(self) -> None:  # noqa: B027 - 可选生命周期钩子
        """启动后台 worker（如有）。"""

    async def stop(self) -> None:  # noqa: B027
        """优雅停止 worker。"""
