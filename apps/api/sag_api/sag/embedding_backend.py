"""程序内嵌 8-bit 量化 embedding（llama-cpp-python + 受支持 GGUF）。

工作方式：
- `embedding_provider=api`   → 走 zleap-sag 原有 OpenAI 兼容客户端（不变）。
- `embedding_provider=local` → 用 llama-cpp-python 在进程内加载选中的 embedding GGUF，
  通过 monkeypatch 替换 zleap-sag 的 embedding 客户端入口，所有 ingest / search
  链路（factory.get_embedding_client / core.ai.embedding 便捷函数）统一走本地推理。

每个知识库只能使用同一向量维度；切换 BGE-M3 与 Qwen3 embedding 模型后应重新处理文档，
避免与既有 LanceDB 向量混用。
"""

from __future__ import annotations

import asyncio
import os
import threading
from typing import Any

from sag_api.core.config import Settings
from sag_api.core.logging import get_logger

log = get_logger("sag.embedding_local")

_local_singleton: "LocalEmbeddingClient | None" = None
_local_lock = asyncio.Lock()
_init_thread_lock = threading.Lock()
# 保存原始实现，便于卸载/恢复
_original_factory_get = None
_original_embedding_module = {}
_patch_installed = False


class LocalEmbeddingClient:
    """llama.cpp 进程内推理的受支持 embedding GGUF（L2 归一化）。"""

    def __init__(self, model_path: str, *, n_ctx: int = 2048, n_threads: int | None = None) -> None:
        self.model_path = model_path
        self.n_ctx = n_ctx
        self.n_threads = n_threads
        self._llm: Any = None
        self._lock = asyncio.Lock()

    @property
    def fingerprint(self) -> str:
        return f"{self.model_path}|{self.n_ctx}|{self.n_threads}"

    # ── 懒加载（首次调用时载入模型，约 2 秒）──────────────
    def _ensure_loaded(self) -> None:
        if self._llm is not None:
            return
        try:
            from llama_cpp import Llama
        except ImportError as exc:  # pragma: no cover - 依赖缺失时明确报错
            raise RuntimeError(
                "本地 embedding 需要 llama-cpp-python：pip install llama-cpp-python "
                "(Windows 无编译环境时用 --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu)"
            ) from exc
        if not os.path.exists(self.model_path):
            raise RuntimeError(
                f"本地 embedding 模型不存在：{self.model_path}。"
                "请先下载 bge-m3-Q8_0.gguf 到该路径，或切换回 API 模式。"
            )
        log.info("加载本地 embedding 模型：%s", self.model_path)
        kwargs: dict[str, Any] = {
            "model_path": self.model_path,
            "embedding": True,
            "n_ctx": self.n_ctx,
            "verbose": False,
        }
        if self.n_threads:
            kwargs["n_threads"] = self.n_threads
        self._llm = Llama(**kwargs)
        log.info("本地 embedding 模型就绪（L2 归一化）：%s", self.model_path)

    async def generate(self, text: str) -> list[float]:
        async with self._lock:
            return await asyncio.to_thread(self._generate_sync, text)

    def _generate_sync(self, text: str) -> list[float]:
        self._ensure_loaded()
        return [float(x) for x in self._llm.embed([text], normalize=True)[0]]

    async def batch_generate(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        async with self._lock:
            return await asyncio.to_thread(self._batch_generate_sync, texts)

    def _batch_generate_sync(self, texts: list[str]) -> list[list[float]]:
        self._ensure_loaded()
        return [[float(x) for x in row] for row in self._llm.embed(list(texts), normalize=True)]

    async def close(self) -> None:
        self._llm = None

    def warmup(self) -> None:
        """预热：加载模型并跑一次小推理，避免首个真实调用等待。"""
        self._ensure_loaded()
        self._llm.embed(["warmup"], normalize=True)


def local_embedding_status(settings: Settings) -> dict:
    """本地内嵌状态（供设置页展示：模型文件是否存在/大小/是否可加载）。"""
    model_path = settings.embedding_local_model_path()
    exists = os.path.exists(model_path)
    info: dict[str, Any] = {
        "provider": settings.embedding_provider,
        "model_path": model_path,
        "model_file": settings.embedding_local_model_file,
        "model_exists": exists,
        "model_size_mb": round(os.path.getsize(model_path) / 1024 / 1024, 1) if exists else None,
        "model_fingerprint": None,
        "ready": False,
        "error": None,
    }
    if exists:
        try:
            stat = os.stat(model_path)
            info["model_fingerprint"] = f"{stat.st_size}-{int(stat.st_mtime)}"
        except OSError:
            pass
    if settings.embedding_provider != "local":
        return info
    if not exists:
        info["error"] = f"模型文件不存在：{model_path}"
        return info
    try:
        _local_client()._ensure_loaded()
        info["ready"] = True
    except Exception as exc:  # noqa: BLE001
        info["error"] = str(exc)
    return info


def ensure_embedding_ready(settings: Settings) -> dict:
    """启动等待：local 模式同步加载并预热模型（阻塞几秒），API ready 前模型已就绪。

    - api 模式：直接返回（无需等待）。
    - local 模式：模型文件缺失或加载失败 → 记录错误并返回（不阻塞 API，UI 会提示）。
    """
    if settings.embedding_provider != "local":
        return {"provider": "api", "ready": True, "error": None}
    try:
        client = _local_client()
        client.warmup()
        log.info("本地 embedding 模型已预热，启动就绪（%s）", client.model_path)
        return {"provider": "local", "ready": True, "error": None}
    except Exception as exc:  # noqa: BLE001
        log.error("本地 embedding 模型加载失败（启动继续，UI 将提示）：%s", exc)
        return {"provider": "local", "ready": False, "error": str(exc)}


def _local_client() -> LocalEmbeddingClient:
    global _local_singleton
    from sag_api.core.config import settings

    n_threads = settings.embedding_local_n_threads or None
    desired = LocalEmbeddingClient(
        settings.embedding_local_model_path(),
        n_ctx=settings.embedding_local_n_ctx,
        n_threads=n_threads,
    )
    if _local_singleton is None or _local_singleton.fingerprint != desired.fingerprint:
        _local_singleton = desired
    return _local_singleton


async def _factory_get_embedding_client(scenario: str = "general") -> LocalEmbeddingClient:
    """替换 zleap-sag factory.get_embedding_client：local 模式返回本地客户端。"""
    client = _local_client()
    # 预热：首次调用在后台线程加载模型，避免阻塞事件循环
    if client._llm is None:
        await asyncio.to_thread(client._ensure_loaded)
    return client


def install_embedding_backend(settings: Settings) -> None:
    """根据 settings.embedding_provider 安装/更新 embedding 后端补丁。

    - api   → 恢复 zleap-sag 原生 OpenAI 兼容实现。
    - local → 替换 factory 与 core.ai.embedding 入口为本地推理。
    """
    global _patch_installed, _original_factory_get, _original_embedding_module

    import zleap.sag.core.ai as zl_ai
    import zleap.sag.core.ai.embedding as zl_embedding
    import zleap.sag.core.ai.factory as zl_factory

    if _original_factory_get is None:
        _original_factory_get = zl_factory.get_embedding_client
    if not _original_embedding_module:
        _original_embedding_module = {
            "get_embedding_client": zl_embedding.get_embedding_client,
            "generate_embedding": zl_embedding.generate_embedding,
            "batch_generate_embedding": zl_embedding.batch_generate_embedding,
            "package_get": getattr(zl_ai, "get_embedding_client", None),
            "package_generate": getattr(zl_ai, "generate_embedding", None),
            "package_batch": getattr(zl_ai, "batch_generate_embedding", None),
        }

    if settings.embedding_provider == "local":
        zl_factory.get_embedding_client = _factory_get_embedding_client
        zl_embedding.get_embedding_client = lambda: _local_client()  # noqa: E731
        zl_embedding.generate_embedding = _local_client().generate  # noqa: E731
        zl_embedding.batch_generate_embedding = _local_client().batch_generate  # noqa: E731
        if _original_embedding_module["package_generate"] is not None:
            zl_ai.generate_embedding = _local_client().generate  # noqa: E731
        if _original_embedding_module["package_batch"] is not None:
            zl_ai.batch_generate_embedding = _local_client().batch_generate  # noqa: E731
        log.info("本地 embedding 后端已启用（GGUF 程序内嵌）")
    else:
        zl_factory.get_embedding_client = _original_factory_get
        zl_embedding.get_embedding_client = _original_embedding_module["get_embedding_client"]
        zl_embedding.generate_embedding = _original_embedding_module["generate_embedding"]
        zl_embedding.batch_generate_embedding = _original_embedding_module["batch_generate_embedding"]
        if _original_embedding_module["package_generate"] is not None:
            zl_ai.generate_embedding = _original_embedding_module["package_generate"]
        if _original_embedding_module["package_batch"] is not None:
            zl_ai.batch_generate_embedding = _original_embedding_module["package_batch"]
        log.info("embedding 后端为 API 模式（OpenAI 兼容）")
    _patch_installed = True


def uninstall_embedding_backend() -> None:
    """恢复 zleap-sag 原生 embedding 实现（主要用于测试）。"""
    global _patch_installed
    if not _patch_installed:
        return
    import zleap.sag.core.ai as zl_ai
    import zleap.sag.core.ai.embedding as zl_embedding
    import zleap.sag.core.ai.factory as zl_factory

    if _original_factory_get is not None:
        zl_factory.get_embedding_client = _original_factory_get
    if _original_embedding_module:
        zl_embedding.get_embedding_client = _original_embedding_module["get_embedding_client"]
        zl_embedding.generate_embedding = _original_embedding_module["generate_embedding"]
        zl_embedding.batch_generate_embedding = _original_embedding_module["batch_generate_embedding"]
        if _original_embedding_module["package_generate"] is not None:
            zl_ai.generate_embedding = _original_embedding_module["package_generate"]
        if _original_embedding_module["package_batch"] is not None:
            zl_ai.batch_generate_embedding = _original_embedding_module["package_batch"]
    _patch_installed = False
