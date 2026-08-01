"""Native GGUF cross-encoder reranking without using chat generation.

The normal ``llama-cpp-python`` package exposes embeddings but not the native
``pooling=rank`` adapter needed by BGE/Qwen reranker GGUFs.  The adapter is
therefore discovered explicitly; a missing or incompatible runtime is an
actionable error and callers can retain the fused retrieval order.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

from sag_api.sag.local_model_catalog import ModelKind, get_model_spec


class LocalRerankerUnavailable(RuntimeError):
    """A local model or its native cross-encoder runtime is unavailable."""


class _RankRuntime(Protocol):
    def rank(self, query: str, documents: list[str]) -> Sequence[float]: ...


RuntimeFactory = Callable[..., _RankRuntime | None]


def _repair_existing_sag_data_path(model_path: str) -> str:
    """Repair a legacy ``<drive>\\sag.data`` typo only when its target exists."""
    original = Path(model_path)
    if original.is_file():
        return str(original)
    for malformed, corrected in (("\\sag.data\\", "\\sag\\.data\\"), ("/sag.data/", "/sag/.data/")):
        if malformed not in model_path:
            continue
        candidate = Path(model_path.replace(malformed, corrected, 1))
        if candidate.is_file():
            return str(candidate)
    return model_path


def _create_native_runtime(
    model_path: str,
    *,
    n_ctx: int,
    n_threads: int | None,
) -> _RankRuntime:
    """Load a GGUF runtime that exposes native scalar cross-encoder scores."""
    try:
        from llama_cpp import LLAMA_POOLING_TYPE_RANK
        from llama_cpp.llama_embedding import LlamaEmbedding
    except ImportError as exc:
        raise LocalRerankerUnavailable(
            "The native reranker runtime is unavailable. Install a llama-cpp-python "
            "build that provides LlamaEmbedding with pooling=rank."
        ) from exc

    kwargs: dict[str, Any] = {
        "model_path": model_path,
        "pooling_type": LLAMA_POOLING_TYPE_RANK,
        "n_ctx": n_ctx,
        "verbose": False,
    }
    if n_threads:
        kwargs["n_threads"] = n_threads
    runtime = LlamaEmbedding(**kwargs)
    if not callable(getattr(runtime, "rank", None)):
        raise LocalRerankerUnavailable(
            "The installed llama-cpp-python build does not expose native reranker support."
        )
    return runtime


class LocalReranker:
    """Lazy, in-process native GGUF reranker with a small testable seam."""

    def __init__(
        self,
        model_path: str,
        *,
        n_ctx: int = 4096,
        n_threads: int | None = None,
        runtime_factory: RuntimeFactory | None = None,
    ) -> None:
        self.model_path = _repair_existing_sag_data_path(model_path)
        self.n_ctx = n_ctx
        self.n_threads = n_threads
        self._runtime_factory = runtime_factory or _create_native_runtime
        self._runtime: _RankRuntime | None = None
        self._lock = threading.Lock()

    @property
    def fingerprint(self) -> str:
        return f"{self.model_path}|{self.n_ctx}|{self.n_threads}"

    def _ensure_loaded(self) -> _RankRuntime:
        if self._runtime is not None:
            return self._runtime
        if not os.path.isfile(self.model_path):
            raise LocalRerankerUnavailable(f"Local reranker model does not exist: {self.model_path}")
        spec = get_model_spec(os.path.basename(self.model_path))
        if spec is None or spec.kind is not ModelKind.RERANKER:
            raise LocalRerankerUnavailable(
                f"{os.path.basename(self.model_path)} is not a supported reranker model"
            )
        runtime = self._runtime_factory(
            self.model_path,
            n_ctx=self.n_ctx,
            n_threads=self.n_threads,
        )
        if runtime is None or not callable(getattr(runtime, "rank", None)):
            raise LocalRerankerUnavailable("The native reranker runtime is unavailable.")
        self._runtime = runtime
        return runtime

    def rank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        with self._lock:
            scores = self._ensure_loaded().rank(query, documents)
            if len(scores) != len(documents):
                raise RuntimeError("Local reranker returned an unexpected score count")
            try:
                return [float(score) for score in scores]
            except (TypeError, ValueError) as exc:
                raise RuntimeError("Local reranker returned invalid scores") from exc
