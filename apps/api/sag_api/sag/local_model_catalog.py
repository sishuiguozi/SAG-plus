"""The audited, on-demand local model catalog used by every model surface."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class ModelKind(StrEnum):
    EMBEDDING = "embedding"
    RERANKER = "reranker"


@dataclass(frozen=True, slots=True)
class ModelSpec:
    file_name: str
    label: str
    kind: ModelKind
    runtime: str
    source_url: str
    relative_dir: str
    dimensions: int | None = None
    size_mb: int | None = None


MODEL_SPECS = (
    ModelSpec(
        "bge-m3-Q8_0.gguf", "BGE-M3 Q8", ModelKind.EMBEDDING, "llama_cpp",
        "https://huggingface.co/gpustack/bge-m3-GGUF/resolve/main/bge-m3-Q8_0.gguf",
        "embedding", 1024, 610,
    ),
    ModelSpec(
        "Qwen3-Embedding-0.6B-Q8_0.gguf", "Qwen3 Embedding 0.6B Q8", ModelKind.EMBEDDING, "llama_cpp",
        "https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/main/Qwen3-Embedding-0.6B-Q8_0.gguf",
        "embedding", 1024, 640,
    ),
    ModelSpec(
        "Qwen3-Embedding-4B-Q8_0.gguf", "Qwen3 Embedding 4B Q8", ModelKind.EMBEDDING, "llama_cpp",
        "https://huggingface.co/Qwen/Qwen3-Embedding-4B-GGUF/resolve/main/Qwen3-Embedding-4B-Q8_0.gguf",
        "embedding", 2560, 4280,
    ),
    ModelSpec(
        "bge-reranker-v2-m3-q8_0.gguf", "BGE Reranker v2 M3 Q8", ModelKind.RERANKER, "crispembed",
        "https://huggingface.co/cstr/bge-reranker-v2-m3-GGUF/resolve/main/bge-reranker-v2-m3-q8_0.gguf",
        "reranker", None, 582,
    ),
    ModelSpec(
        "qwen3-reranker-0.6b-q8_0.gguf", "Qwen3 Reranker 0.6B Q8", ModelKind.RERANKER, "llama_cpp",
        "https://huggingface.co/ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF/resolve/main/qwen3-reranker-0.6b-q8_0.gguf",
        "reranker", None, 639,
    ),
    ModelSpec(
        "Qwen3-Reranker-4B-Q8_0.gguf", "Qwen3 Reranker 4B Q8", ModelKind.RERANKER, "llama_cpp",
        "https://huggingface.co/sinjab/Qwen3-Reranker-4B-Q8_0-GGUF/resolve/main/Qwen3-Reranker-4B-Q8_0.gguf",
        "reranker", None, 4280,
    ),
)

MODEL_BY_FILE = MappingProxyType({spec.file_name: spec for spec in MODEL_SPECS})


def get_model_spec(file_name: str) -> ModelSpec | None:
    return MODEL_BY_FILE.get(file_name)


def specs_for(kind: ModelKind) -> tuple[ModelSpec, ...]:
    return tuple(spec for spec in MODEL_SPECS if spec.kind is kind)
