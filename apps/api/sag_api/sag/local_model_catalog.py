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
    sha256: str | None = None


MODEL_SPECS = (
    ModelSpec(
        "bge-m3-Q8_0.gguf", "BGE-M3 Q8", ModelKind.EMBEDDING, "llama_cpp",
        "https://huggingface.co/gpustack/bge-m3-GGUF/resolve/main/bge-m3-Q8_0.gguf",
        "embedding", 1024, 610, "950f4a8e5e19477a6d3c26d2f162233c20002c601f75e4b002e3239997821167",
    ),
    ModelSpec(
        "Qwen3-Embedding-0.6B-Q8_0.gguf", "Qwen3 Embedding 0.6B Q8", ModelKind.EMBEDDING, "llama_cpp",
        "https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/main/Qwen3-Embedding-0.6B-Q8_0.gguf",
        "embedding", 1024, 640, "06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439",
    ),
    ModelSpec(
        "Qwen3-Embedding-4B-Q8_0.gguf", "Qwen3 Embedding 4B Q8", ModelKind.EMBEDDING, "llama_cpp",
        "https://huggingface.co/Qwen/Qwen3-Embedding-4B-GGUF/resolve/main/Qwen3-Embedding-4B-Q8_0.gguf",
        "embedding", 2560, 4280, "b60ae5ce2dd6a0b77f82cadf21def1f310a3e10cde380ad0081b07a9d416949d",
    ),
    ModelSpec(
        "bge-reranker-v2-m3-q8_0.gguf", "BGE Reranker v2 M3 Q8", ModelKind.RERANKER, "llama_cpp_rank",
        "https://huggingface.co/cstr/bge-reranker-v2-m3-GGUF/resolve/main/bge-reranker-v2-m3-q8_0.gguf",
        "reranker", None, 582, "63e5a900e1605e3be3a96d94b6ac63f0f9f4efe66b2bef07890587b6412c443e",
    ),
    ModelSpec(
        "qwen3-reranker-0.6b-q8_0.gguf", "Qwen3 Reranker 0.6B Q8", ModelKind.RERANKER, "llama_cpp_rank",
        "https://huggingface.co/ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF/resolve/main/qwen3-reranker-0.6b-q8_0.gguf",
        "reranker", None, 639, "22c9979ce4fbcdc5acdc310c6641c32797eff1aa980b8f7a2db8a8ea23429a48",
    ),
    ModelSpec(
        "Qwen3-Reranker-4B-Q8_0.gguf", "Qwen3 Reranker 4B Q8", ModelKind.RERANKER, "llama_cpp_rank",
        "https://huggingface.co/sinjab/Qwen3-Reranker-4B-Q8_0-GGUF/resolve/main/Qwen3-Reranker-4B-Q8_0.gguf",
        "reranker", None, 4280, "143f21b1cba67d328d32dd69daa282d79cb4c1e95398251caa7e48813ec98451",
    ),
)

MODEL_BY_FILE = MappingProxyType({spec.file_name: spec for spec in MODEL_SPECS})


def get_model_spec(file_name: str) -> ModelSpec | None:
    return MODEL_BY_FILE.get(file_name)


def specs_for(kind: ModelKind) -> tuple[ModelSpec, ...]:
    return tuple(spec for spec in MODEL_SPECS if spec.kind is kind)
